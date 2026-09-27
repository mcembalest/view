package main

import (
	"os"
	"os/exec"
	"testing"
)

// Starts the real worker (first run installs PyTorch etc. through uv), so it is skipped in CI.
func TestGokitPython(t *testing.T) {
	if _, err := exec.LookPath("uv"); err != nil || os.Getenv("CI") != "" {
		t.Skip("needs uv; skipped in CI")
	}
	w, err := gokitPython()
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	if err := w.Call(map[string]string{"cmd": "nope"}, nil); err == nil {
		t.Fatal("expected an error for an unknown command")
	}
}
