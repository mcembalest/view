// view: see a folder's text and image files as a semantic map in the browser, in 3D and 2D.
package main

import (
	"embed"
	"io/fs"

	"github.com/mcembalest/view/internal/app"
)

//go:embed web/dist
var web embed.FS

//go:embed python
var python embed.FS

func main() {
	dist, _ := fs.Sub(web, "web/dist")
	app.Main(dist, python)
}
