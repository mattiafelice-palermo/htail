# htail 0.11.0

## New features

- Local `/` search now opens in **Simple** mode by default: ordinary characters are literal, `*` matches any text, and `?` matches one character. Press `Tab` inside the modal to switch explicitly between Simple and Regex modes.
- Added `g` global live search across all currently watched files. Results update while typing and show pane, source line and a matching preview.
- In global search, use `↑` / `↓` to choose a result and `Enter` to focus that pane, jump to the matching source line and make the query the pane's active search so `n` / `N` continue naturally.
- Global search also supports the same explicit Simple / Regex toggle with `Tab`.

## Bug fixes / safety

- Regex punctuation is treated literally in Simple mode, so searches such as `a.b`, `[045]` or paths do not require escaping.
- Search modals now ignore mouse clicks on the dimmed background instead of changing pane focus behind the dialog.
- Existing `h` persistent highlight rules remain explicitly regex-based; their behavior is unchanged.
