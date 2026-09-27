package main

import (
	"bufio"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"sync"
)

// Embedded Python worker, run with uv so its Python and dependencies install themselves on
// first use. Edit python/worker.py and python/pyproject.toml; relock with `uv lock --project python`.
// Protocol: one JSON request per stdin line, one JSON response per stdout line; stderr passes through.

//go:embed python/pyproject.toml python/uv.lock python/*.py
var gokitPythonFiles embed.FS

type gokitPythonWorker struct {
	cmd *exec.Cmd
	in  io.WriteCloser
	out *bufio.Scanner
	mu  sync.Mutex
}

// gokitPython starts the worker. Files are written once to a user cache folder named by their hash.
func gokitPython() (*gokitPythonWorker, error) {
	if _, err := exec.LookPath("uv"); err != nil {
		return nil, errors.New("uv is required: https://docs.astral.sh/uv/getting-started/installation/")
	}
	files := map[string][]byte{}
	err := fs.WalkDir(gokitPythonFiles, "python", func(p string, d fs.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			files[p], err = gokitPythonFiles.ReadFile(p)
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(files))
	for n := range files {
		names = append(names, n)
	}
	sort.Strings(names)
	h := sha256.New()
	for _, n := range names {
		fmt.Fprintf(h, "%s\x00%d\x00", n, len(files[n]))
		h.Write(files[n])
	}
	base, err := os.UserCacheDir()
	if err != nil {
		return nil, err
	}
	dir := filepath.Join(base, "view", "python-"+hex.EncodeToString(h.Sum(nil))[:12])
	for _, n := range names {
		target := filepath.Join(dir, filepath.FromSlash(n[len("python/"):]))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(target, files[n], 0o644); err != nil {
			return nil, err
		}
	}
	cmd := exec.Command("uv", "run", "--locked", "--quiet", "--project", dir, "python", filepath.Join(dir, "worker.py"))
	cmd.Stderr = os.Stderr
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 1<<20), 1<<28)
	return &gokitPythonWorker{cmd: cmd, in: in, out: sc}, nil
}

// Call sends req and decodes the response into resp. A response with an "error" field fails.
func (w *gokitPythonWorker) Call(req, resp any) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	b, err := json.Marshal(req)
	if err != nil {
		return err
	}
	if _, err := w.in.Write(append(b, '\n')); err != nil {
		return fmt.Errorf("python worker: %w", err)
	}
	if !w.out.Scan() {
		if err := w.out.Err(); err != nil {
			return fmt.Errorf("python worker: %w", err)
		}
		return errors.New("python worker exited")
	}
	var failed struct {
		Error *string `json:"error"`
	}
	if json.Unmarshal(w.out.Bytes(), &failed) == nil && failed.Error != nil {
		return fmt.Errorf("python worker: %s", *failed.Error)
	}
	if resp == nil {
		return nil
	}
	return json.Unmarshal(w.out.Bytes(), resp)
}

// Close ends the worker by closing its input.
func (w *gokitPythonWorker) Close() error {
	w.in.Close()
	return w.cmd.Wait()
}
