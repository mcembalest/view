package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Model aliases the worker knows, with first-run download sizes (MB).
var knownModels = []struct {
	alias, repo, about string
	mb                 int
}{
	{"mobileclip2-s0", "timm/MobileCLIP2-S0-OpenCLIP", "images + text; fast", 300},
	{"mobileclip2", "timm/MobileCLIP2-S4-OpenCLIP", "images + text; sharper", 1800},
	{"siglip2", "timm/ViT-SO400M-16-SigLIP2-384", "images + text; best image search, slow", 4500},
	{"clip", "sentence-transformers/clip-ViT-B-32", "images + text", 600},
	{"qwen3-vl", "Qwen/Qwen3-VL-Embedding-2B", "images + text; reads text in screenshots, very slow", 4300},
	{"minilm", "sentence-transformers/all-MiniLM-L6-v2", "text only; images by name", 90},
	{"qwen3-text", "Qwen/Qwen3-Embedding-0.6B", "text only; use with -t", 1200},
}

const libsMB = 300 // PyTorch and friends, installed once by uv

func modelHelp() string {
	var b strings.Builder
	for _, m := range knownModels {
		fmt.Fprintf(&b, "                %-15s %5s  %s\n", m.alias, sizeLabel(m.mb), m.about)
	}
	return b.String()
}

func sizeLabel(mb int) string {
	if mb >= 1000 {
		return fmt.Sprintf("%.1fGB", float64(mb)/1000)
	}
	return fmt.Sprintf("%dMB", mb)
}

// hfCached reports whether a Hugging Face repo is already in the local cache.
func hfCached(repo string) bool {
	home := os.Getenv("HF_HOME")
	if home == "" {
		h, _ := os.UserHomeDir()
		home = filepath.Join(h, ".cache", "huggingface")
	}
	_, err := os.Stat(filepath.Join(home, "hub", "models--"+strings.ReplaceAll(repo, "/", "--")))
	return err == nil
}

// downloadLine describes what the first run will download, or "" if everything is cached.
func downloadLine(models []string) string {
	var parts []string
	total := 0
	if !pythonReady() {
		parts, total = append(parts, "libraries "+sizeLabel(libsMB)), libsMB
	}
	for _, name := range models {
		if name == "" {
			continue
		}
		known := false
		for _, m := range knownModels {
			if m.alias == name || m.repo == name {
				known = true
				if !hfCached(m.repo) {
					parts, total = append(parts, name+" "+sizeLabel(m.mb)), total+m.mb
				}
			}
		}
		if !known && !hfCached(strings.TrimPrefix(name, "open_clip:")) {
			parts = append(parts, name+" (size unknown)")
		}
	}
	if len(parts) == 0 {
		return ""
	}
	if total == 0 {
		return "downloads once: " + strings.Join(parts, ", ")
	}
	return fmt.Sprintf("downloads ~%s once: %s", sizeLabel(total), strings.Join(parts, ", "))
}

// pythonReady reports whether the worker's Python environment is installed (see kit.Python).
func pythonReady() bool {
	base, err := os.UserCacheDir()
	return err == nil && exists(filepath.Join(base, "view", "python", ".venv"))
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
