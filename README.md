# view

## Install

```sh
uv tool install -e ~/Desktop/repos/view
```

## Run

```sh
view [folder]                 # default: current folder
view folder -m clip -m minilm # several models
view folder --jev             # add TypeSafe Jev answers (needs TYPESAFE_API_KEY)
view folder -y                # skip confirmation
```

Scan prompt: `enter` index, `l` list files, `e` edit `.view/ignore`, `q` quit.

Models: `mobileclip2` (default), `siglip2`, `clip`, `qwen3-vl`, `minilm`, `qwen3-text`, any sentence-transformers id, or `open_clip:ARCH/PRETRAINED`.

Jev questions: `.view/jev.json`.

Output: `folder/.view/`.

## Browser

| action | input |
|---|---|
| orbit / pan / zoom | drag / right-drag / scroll |
| 2D ↔ 3D | `space` |
| filter by path | type in search (`-word` excludes) |
| search by meaning | type, then `enter` |
| color by field | `color` menu |
| show only a group | click legend row (shift-click: add/remove) |
| select region | shift-drag |
| preview | hover point or tile |
| pin, reveal, open | click point or tile |
| clear | `esc` |
