// view: see a folder's text and image files as a semantic map in the browser, in 3D and 2D.
package main

import (
	"crypto/sha1"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

//go:embed web/dist
var webFS embed.FS

const usage = `usage: view [folder] [-m model]... [--jev] [-y] [--port N] [--no-open]

  folder      folder to view (default: current folder)
  -m model    embedding model; repeat to compare several (default: siglip2)
              aliases: siglip2, mobileclip2, clip, qwen3-vl, minilm, qwen3-text
              or any sentence-transformers id, or open_clip:ARCH/PRETRAINED
  --jev       ask TypeSafe Jev the questions in .view/jev.json (needs TYPESAFE_API_KEY)
  -y          skip the index confirmation
  --port N    port (default: any free port)
  --no-open   don't open a browser tab
`

type options struct {
	folder string
	models []string
	jev    bool
	yes    bool
	port   int
	noOpen bool
}

func parseArgs(args []string) (options, error) {
	o := options{folder: "."}
	for i := 0; i < len(args); i++ {
		a := args[i]
		next := func() (string, error) {
			if i+1 >= len(args) {
				return "", fmt.Errorf("%s needs a value", a)
			}
			i++
			return args[i], nil
		}
		switch a {
		case "-h", "--help":
			fmt.Print(usage)
			os.Exit(0)
		case "-m", "--model":
			v, err := next()
			if err != nil {
				return o, err
			}
			o.models = append(o.models, v)
		case "--jev":
			o.jev = true
		case "-y", "--yes":
			o.yes = true
		case "--no-open":
			o.noOpen = true
		case "--port":
			v, err := next()
			if err != nil {
				return o, err
			}
			if o.port, err = strconv.Atoi(v); err != nil {
				return o, fmt.Errorf("--port: %v", err)
			}
		default:
			if strings.HasPrefix(a, "-") {
				return o, fmt.Errorf("unknown flag %s", a)
			}
			o.folder = a
		}
	}
	if len(o.models) == 0 {
		o.models = []string{"siglip2"}
	}
	return o, nil
}

func main() {
	gokitStartup()
	o, err := parseArgs(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "view: %v\n\n%s", err, usage)
		os.Exit(2)
	}
	if err := run(o); err != nil {
		fmt.Fprintf(os.Stderr, "view: %v\n", err)
		os.Exit(1)
	}
}

func run(o options) error {
	root, err := filepath.Abs(o.folder)
	if err != nil {
		return err
	}
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return fmt.Errorf("not a folder: %s", root)
	}
	if o.jev {
		loadEnv(root)
	}
	extra := func(files []File) []string {
		lines := []string{"models    " + strings.Join(o.models, ", "), "writes    " + filepath.Join(root, ".view") + "/"}
		if o.jev {
			toks, cost := jevEstimate(files, jevQuestions(root))
			lines = append(lines, fmt.Sprintf("jev       ~%.1fM tokens ≈ $%.2f; sends file names, text, and text read from images to TypeSafe", float64(toks)/1e6, cost))
		}
		return lines
	}
	files, err := confirm(root, extra, o.yes)
	if err != nil {
		return err
	}

	w, err := gokitPython()
	if err != nil {
		return err
	}
	defer w.Close()
	rows := make([][]any, len(files))
	for i, f := range files {
		rows[i] = []any{f.Rel, f.Kind, f.Size, f.Mtime}
	}
	if err := w.Call(map[string]any{"cmd": "build", "root": root, "files": rows, "models": o.models}, nil); err != nil {
		return err
	}

	meta := map[string]any{"root": root, "items": rows, "columns": map[string]any{}}
	models := []map[string]string{}
	for _, m := range o.models {
		models = append(models, map[string]string{"name": m, "slug": slug(m)})
	}
	meta["models"] = models
	if o.jev {
		answers, err := jevRun(root, files)
		if err != nil {
			return err
		}
		cols := meta["columns"].(map[string]any)
		for q := range jevQuestions(root) {
			col := make([]any, len(files))
			for i, f := range files {
				col[i] = answers[f.Rel][q]
			}
			cols["jev · "+q] = col
		}
	}
	b, _ := json.Marshal(meta)
	if err := os.WriteFile(filepath.Join(root, ".view", "map", "meta.json"), b, 0o644); err != nil {
		return err
	}
	return serve(root, files, w, o)
}

var slugRe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func slug(s string) string { return slugRe.ReplaceAllString(s, "_") }

// key identifies a file version; the worker uses the same format for its caches and thumbnail names.
func key(f File) string { return fmt.Sprintf("%s|%d|%d", f.Rel, f.Size, f.Mtime) }

func thumbName(f File) string {
	h := sha1.Sum([]byte(key(f)))
	return hex.EncodeToString(h[:])[:16] + ".jpg"
}
