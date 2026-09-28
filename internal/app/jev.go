package app

import (
	"bytes"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Optional: ask TypeSafe Jev typed questions about every file (https://docs.typesafe.ai/api).
// Jev reads text only: text files send their content; images send their name and the text read from them.

const (
	jevURL      = "https://api.typesafe.ai/v1/systemone"
	jevPerMTok  = 0.042
	jevTextSize = 6000
)

const defaultQuestions = `{
  "kind": {
    "type": "choice",
    "instructions": "What is this file most likely to be?",
    "criteria": {
      "personal photo": "A photo taken with a camera or phone",
      "screenshot": "A capture of a screen: app, website, social post, chat",
      "diagram or chart": "A figure, plot, diagram, or slide",
      "artwork or illustration": null,
      "notes or writing": "Prose, notes, journal, essay",
      "code or data": null,
      "other": null
    }
  },
  "work": {
    "type": "noul",
    "instructions": "Is this file most likely related to work, research, or study (rather than personal life)?"
  }
}
`

// loadEnv reads TYPESAFE_API_KEY from a .env in the viewed folder or the current folder.
func loadEnv(root string) {
	for _, d := range []string{root, "."} {
		if os.Getenv("TYPESAFE_API_KEY") != "" {
			return
		}
		b, _ := os.ReadFile(filepath.Join(d, ".env"))
		for _, line := range strings.Split(string(b), "\n") {
			if k, v, ok := strings.Cut(line, "="); ok && strings.TrimSpace(k) == "TYPESAFE_API_KEY" {
				os.Setenv("TYPESAFE_API_KEY", strings.Trim(strings.TrimSpace(v), `"'`))
			}
		}
	}
}

func jevQuestions(root string) map[string]json.RawMessage {
	p := filepath.Join(root, ".view", "jev.json")
	if _, err := os.Stat(p); err != nil {
		os.MkdirAll(filepath.Dir(p), 0o755)
		os.WriteFile(p, []byte(defaultQuestions), 0o644)
	}
	b, _ := os.ReadFile(p)
	q := map[string]json.RawMessage{}
	if err := json.Unmarshal(b, &q); err != nil {
		fmt.Fprintf(os.Stderr, "view: %s: %v\n", p, err)
	}
	return q
}

func jevEstimate(files []File, questions map[string]json.RawMessage) (int, float64) {
	b, _ := json.Marshal(questions)
	toks := 0
	for _, f := range files {
		n := int64(300)
		if f.Kind == "text" {
			n = min(f.Size, jevTextSize)
		}
		toks += int(n)/4 + len(b)/4 + 60
	}
	return toks, float64(toks) / 1e6 * jevPerMTok
}

func readHead(path string, n int) string {
	fh, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer fh.Close()
	b, _ := io.ReadAll(io.LimitReader(fh, int64(n)))
	return strings.ToValidUTF8(string(b), "")
}

func loadOCR(root string) map[string]string {
	m := map[string]string{}
	b, _ := os.ReadFile(filepath.Join(root, ".view", "ocr.json"))
	json.Unmarshal(b, &m)
	return m
}

func jevState(root string, f File, ocr map[string]string) map[string]any {
	folder, name := filepath.Split(f.Rel)
	s := map[string]any{
		"filename": name, "folder": strings.TrimSuffix(folder, "/"),
		"modified": time.Unix(f.Mtime, 0).Format("2006-01-02"), "size_kb": f.Size / 1024,
	}
	if f.Kind == "text" {
		s["content"] = readHead(filepath.Join(root, f.Rel), jevTextSize)
	} else {
		s["text_in_image"] = ocr[key(f)]
	}
	return s
}

func jevAsk(body []byte) (map[string]any, error) {
	for attempt := 0; ; attempt++ {
		req, _ := http.NewRequest("POST", jevURL, bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+os.Getenv("TYPESAFE_API_KEY"))
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err == nil && resp.StatusCode == 200 {
			var r struct {
				Answers map[string]map[string]any `json:"answers"`
			}
			err = json.NewDecoder(resp.Body).Decode(&r)
			resp.Body.Close()
			out := map[string]any{}
			for q, a := range r.Answers {
				out[q] = a[a["type"].(string)]
			}
			return out, err
		}
		retry := err != nil
		if resp != nil {
			msg, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			retry = retry || resp.StatusCode == 429 || resp.StatusCode >= 500
			err = fmt.Errorf("%s: %s", resp.Status, bytes.TrimSpace(msg))
		}
		if !retry || attempt == 5 {
			return nil, err
		}
		time.Sleep(time.Duration(1<<attempt) * time.Second)
	}
}

// jevRun returns {file.Rel: {question: answer}}, cached per file and question set in .view/jev_cache.json.
func jevRun(root string, files []File) (map[string]map[string]any, error) {
	if os.Getenv("TYPESAFE_API_KEY") == "" {
		return nil, fmt.Errorf("--jev needs TYPESAFE_API_KEY (environment or .env)")
	}
	questions := jevQuestions(root)
	qb, _ := json.Marshal(questions)
	sum := sha1.Sum(qb)
	qhash := hex.EncodeToString(sum[:])[:10]
	cpath := filepath.Join(root, ".view", "jev_cache.json")
	cache := map[string]map[string]any{}
	if b, err := os.ReadFile(cpath); err == nil {
		json.Unmarshal(b, &cache)
	}
	ck := func(f File) string { return key(f) + "|" + qhash }
	var todo []File
	for _, f := range files {
		if _, ok := cache[ck(f)]; !ok {
			todo = append(todo, f)
		}
	}
	fmt.Printf("\n  jev  %d to ask · %d cached\n", len(todo), len(files)-len(todo))
	ocr := loadOCR(root)
	var mu sync.Mutex
	jobs, done := make(chan File), 0
	var wg sync.WaitGroup
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for f := range jobs {
				body, _ := json.Marshal(map[string]any{"model": "jev-latest", "state": jevState(root, f, ocr), "questions": questions})
				ans, err := jevAsk(body)
				mu.Lock()
				if err != nil {
					fmt.Fprintf(os.Stderr, "\n    jev failed on %s: %v\n", f.Rel, err)
				} else {
					cache[ck(f)] = ans
				}
				done++
				fmt.Printf("\r    %d/%d", done, len(todo))
				mu.Unlock()
			}
		}()
	}
	for _, f := range todo {
		jobs <- f
	}
	close(jobs)
	wg.Wait()
	if len(todo) > 0 {
		fmt.Println()
		b, _ := json.Marshal(cache)
		os.WriteFile(cpath, b, 0o644)
	}
	out := map[string]map[string]any{}
	for _, f := range files {
		out[f.Rel] = cache[ck(f)]
	}
	return out, nil
}
