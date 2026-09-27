package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os/exec"
	"path/filepath"
	"strconv"
)

func serve(root string, files []File, w *gokitPythonWorker, o options) error {
	mapDir := filepath.Join(root, ".view", "map")
	ocr := loadOCR(root)
	dist, _ := fs.Sub(webFS, "web/dist")
	file := func(r *http.Request) (File, bool) {
		i, err := strconv.Atoi(r.PathValue("i"))
		if err != nil || i < 0 || i >= len(files) {
			return File{}, false
		}
		return files[i], true
	}
	mux := http.NewServeMux()
	mux.Handle("GET /", http.FileServerFS(dist))
	mux.HandleFunc("GET /data/{name}", func(rw http.ResponseWriter, r *http.Request) {
		http.ServeFile(rw, r, filepath.Join(mapDir, filepath.Base(r.PathValue("name"))))
	})
	mux.HandleFunc("GET /thumb/{i}", func(rw http.ResponseWriter, r *http.Request) {
		if f, ok := file(r); ok {
			rw.Header().Set("Cache-Control", "max-age=86400")
			http.ServeFile(rw, r, filepath.Join(root, ".view", "thumbs", thumbName(f)))
			return
		}
		http.NotFound(rw, r)
	})
	// peek: the start of a text file, or the text read from an image
	mux.HandleFunc("GET /peek/{i}", func(rw http.ResponseWriter, r *http.Request) {
		f, ok := file(r)
		if !ok {
			http.NotFound(rw, r)
			return
		}
		rw.Header().Set("Content-Type", "text/plain; charset=utf-8")
		if f.Kind == "text" {
			fmt.Fprint(rw, readHead(filepath.Join(root, f.Rel), 2000))
		} else {
			fmt.Fprint(rw, ocr[key(f)])
		}
	})
	// POSTs need a custom header, which forces a CORS preflight: other websites can't trigger them.
	api := func(h http.HandlerFunc) http.HandlerFunc {
		return func(rw http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-View") != "1" {
				http.Error(rw, "forbidden", http.StatusForbidden)
				return
			}
			h(rw, r)
		}
	}
	mux.HandleFunc("POST /api/search", api(func(rw http.ResponseWriter, r *http.Request) {
		var q struct{ Q, Model string }
		json.NewDecoder(r.Body).Decode(&q)
		var resp struct{ Results json.RawMessage }
		if err := w.Call(map[string]any{"cmd": "search", "q": q.Q, "model": q.Model}, &resp); err != nil {
			http.Error(rw, err.Error(), http.StatusInternalServerError)
			return
		}
		rw.Write(resp.Results)
	}))
	mux.HandleFunc("POST /api/{action}/{i}", api(func(rw http.ResponseWriter, r *http.Request) {
		f, ok := file(r)
		action := r.PathValue("action")
		if !ok || action != "reveal" && action != "open" {
			http.NotFound(rw, r)
			return
		}
		args := []string{filepath.Join(root, f.Rel)}
		if action == "reveal" {
			args = append([]string{"-R"}, args...)
		}
		exec.Command("open", args...).Run()
		fmt.Fprint(rw, `{"ok":true}`)
	}))

	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", o.port))
	if err != nil {
		return err
	}
	url := fmt.Sprintf("http://%s/", ln.Addr())
	fmt.Printf("\n  viewing  %s   (ctrl-c to stop)\n\n", url)
	if !o.noOpen {
		exec.Command("open", url).Start()
	}
	return http.Serve(ln, mux)
}
