package main

import (
	"fmt"
	"os"
	"runtime"
	"runtime/debug"
)

var gokitVersion, gokitCommit string

func gokitStartup() {
	if len(os.Args) > 1 && (os.Args[1] == "--version" || os.Args[1] == "-v") {
		v, commit, dirty := gokitVersion, gokitCommit, "unknown"
		if info, ok := debug.ReadBuildInfo(); ok {
			if v == "" && info.Main.Version != "(devel)" {
				v = info.Main.Version
			}
			for _, s := range info.Settings {
				switch s.Key {
				case "vcs.revision":
					if commit == "" {
						commit = s.Value
					}
				case "vcs.modified":
					dirty = s.Value
				}
			}
		}
		if v == "" {
			v = "dev"
		}
		if commit == "" {
			commit = "unknown"
		}
		fmt.Printf("view %s (commit=%s dirty=%s %s %s/%s)\n", v, commit, dirty, runtime.Version(), runtime.GOOS, runtime.GOARCH)
		os.Exit(0)
	}

}
