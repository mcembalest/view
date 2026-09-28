package app

import (
	"bufio"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// File is one indexed file. Rel is slash-separated, relative to the viewed folder.
type File struct {
	Rel   string
	Kind  string // "image" | "text"
	Ext   string
	Size  int64
	Mtime int64
}

var kinds = map[string]string{}

func init() {
	for _, e := range strings.Fields("png jpg jpeg gif webp bmp tif tiff heic heif") {
		kinds[e] = "image"
	}
	for _, e := range strings.Fields("txt md markdown rst org tex csv tsv json jsonl yaml yml toml html htm xml py js ts tsx jsx go rs java c h cpp swift rb sh sql ipynb") {
		kinds[e] = "text"
	}
}

const maxBytes = 50_000_000

const defaultIgnore = `# Files matching these patterns are left out of the index (gitignore syntax).
# Paths are relative to the folder being viewed. Save and close to rescan.
#
# examples:
#   drafts/
#   *.json
#   Screenshot*
`

// ignoreRule is one gitignore line compiled to a regexp over slash paths.
type ignoreRule struct {
	re      *regexp.Regexp
	negate  bool
	dirOnly bool
}

func loadIgnore(path string) []ignoreRule {
	b, _ := os.ReadFile(path)
	var rules []ignoreRule
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		r := ignoreRule{}
		if strings.HasPrefix(line, "!") {
			r.negate, line = true, line[1:]
		}
		if strings.HasSuffix(line, "/") {
			r.dirOnly, line = true, strings.TrimSuffix(line, "/")
		}
		anchored := strings.Contains(line, "/")
		line = strings.TrimPrefix(line, "/")
		var re strings.Builder
		for i := 0; i < len(line); i++ {
			switch c := line[i]; {
			case c == '*' && i+1 < len(line) && line[i+1] == '*':
				re.WriteString(".*")
				i++
				if i+1 < len(line) && line[i+1] == '/' {
					i++
				}
			case c == '*':
				re.WriteString("[^/]*")
			case c == '?':
				re.WriteString("[^/]")
			default:
				re.WriteString(regexp.QuoteMeta(string(c)))
			}
		}
		prefix := "^"
		if !anchored {
			prefix = "^(.*/)?"
		}
		r.re = regexp.MustCompile(prefix + re.String() + "$")
		rules = append(rules, r)
	}
	return rules
}

func ignored(rules []ignoreRule, rel string, isDir bool) bool {
	out := false
	for _, r := range rules {
		if (!r.dirOnly || isDir) && r.re.MatchString(rel) {
			out = !r.negate
		}
	}
	return out
}

func countFiles(dir string) int {
	n := 0
	filepath.WalkDir(dir, func(_ string, d fs.DirEntry, _ error) error {
		if d != nil && !d.IsDir() {
			n++
		}
		return nil
	})
	return n
}

// scan walks root and returns files to index plus counts of skipped files by reason.
func scan(root string) ([]File, map[string]int) {
	rules := loadIgnore(filepath.Join(root, ".view", "ignore"))
	var files []File
	skipped := map[string]int{}
	filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || path == root {
			return nil
		}
		rel := filepath.ToSlash(strings.TrimPrefix(path, root+string(filepath.Separator)))
		name := d.Name()
		if d.IsDir() {
			switch {
			case rel == ".view":
			case strings.HasPrefix(name, ".") || name == "node_modules":
				skipped["in hidden/vendored folder  "+rel+"/"] += countFiles(path)
			case ignored(rules, rel, true):
				skipped["in ignored folder  "+rel+"/"] += countFiles(path)
			default:
				return nil
			}
			return filepath.SkipDir
		}
		ext := ""
		if i := strings.LastIndex(name, "."); i > 0 {
			ext = strings.ToLower(name[i+1:])
		}
		info, err := d.Info()
		switch {
		case strings.HasPrefix(name, "."):
			skipped["hidden file"]++
		case ignored(rules, rel, false):
			skipped["matched .view/ignore"]++
		case kinds[ext] == "":
			if ext == "" {
				ext = "(no extension)"
			}
			skipped["."+ext+"  not a text or image type"]++
		case err != nil || !info.Mode().IsRegular():
			skipped["unreadable"]++
		case info.Size() == 0 || info.Size() > maxBytes:
			skipped["empty or over 50MB"]++
		default:
			files = append(files, File{rel, kinds[ext], ext, info.Size(), info.ModTime().Unix()})
		}
		return nil
	})
	return files, skipped
}

type count struct {
	k string
	n int
}

func top(m map[string]int) []count {
	var cs []count
	for k, n := range m {
		cs = append(cs, count{k, n})
	}
	sort.Slice(cs, func(i, j int) bool { return cs[i].n > cs[j].n || cs[i].n == cs[j].n && cs[i].k < cs[j].k })
	return cs
}

func join(cs []count, limit int) string {
	var parts []string
	for i, c := range cs {
		if i == limit {
			parts = append(parts, fmt.Sprintf("%d more", len(cs)-limit))
			break
		}
		parts = append(parts, fmt.Sprintf("%s %d", c.k, c.n))
	}
	return strings.Join(parts, " · ")
}

func summarize(root string, files []File, skipped map[string]int, extra []string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "\n  view  %s\n\n  will index  %d files\n", root, len(files))
	for _, kind := range []string{"image", "text"} {
		exts, n := map[string]int{}, 0
		for _, f := range files {
			if f.Kind == kind {
				exts[f.Ext]++
				n++
			}
		}
		if n > 0 {
			fmt.Fprintf(&b, "    %-6s %7d   %s\n", kind, n, join(top(exts), 99))
		}
	}
	where := map[string]int{}
	for _, f := range files {
		if i := strings.Index(f.Rel, "/"); i >= 0 {
			where[f.Rel[:i+1]]++
		} else {
			where["(top level)"]++
		}
	}
	fmt.Fprintf(&b, "    where    %s\n", join(top(where), 8))
	if len(skipped) > 0 {
		total := 0
		for _, n := range skipped {
			total += n
		}
		fmt.Fprintf(&b, "\n  skipping  %d files\n", total)
		for i, c := range top(skipped) {
			if i == 10 {
				fmt.Fprintf(&b, "    … and %d more reasons\n", len(skipped)-10)
				break
			}
			fmt.Fprintf(&b, "    %7d   %s\n", c.n, c.k)
		}
	}
	b.WriteString("\n")
	for _, x := range extra {
		fmt.Fprintf(&b, "  %s\n", x)
	}
	return b.String()
}

// confirm shows the proposed index and loops until the user accepts it.
func confirm(root string, extra func([]File) []string, yes bool) ([]File, error) {
	ip := filepath.Join(root, ".view", "ignore")
	if _, err := os.Stat(ip); err != nil {
		if err := os.MkdirAll(filepath.Dir(ip), 0o755); err != nil {
			return nil, err
		}
		os.WriteFile(ip, []byte(defaultIgnore), 0o644)
	}
	in := bufio.NewReader(os.Stdin)
	for {
		files, skipped := scan(root)
		fmt.Print(summarize(root, files, skipped, extra(files)))
		if yes {
			return files, nil
		}
		if len(files) == 0 {
			fmt.Println("  nothing to index.")
		}
		fmt.Print("\n  [enter] index these   [l] list files   [e] edit ignore rules   [q] quit  › ")
		ans, err := in.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("no answer")
		}
		switch strings.ToLower(strings.TrimSpace(ans)) {
		case "":
			if len(files) > 0 {
				return files, nil
			}
		case "q":
			os.Exit(0)
		case "l":
			var body strings.Builder
			for _, f := range files {
				fmt.Fprintf(&body, "%-6s %s\n", f.Kind, f.Rel)
			}
			pager := exec.Command("less")
			pager.Stdin, pager.Stdout, pager.Stderr = strings.NewReader(body.String()), os.Stdout, os.Stderr
			if pager.Run() != nil {
				fmt.Print(body.String())
			}
		case "e":
			editor := os.Getenv("EDITOR")
			if editor == "" {
				editor = "nano"
			}
			cmd := exec.Command(editor, ip)
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
			cmd.Run()
		}
	}
}
