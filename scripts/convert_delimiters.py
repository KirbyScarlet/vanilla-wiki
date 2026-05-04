#!/usr/bin/env python3
"""
Convert $...$ and $$...$$ / $$...\\] to \\(...\\) and \\[...\\] in latex-math markdown docs.

Rules:
- Skip content inside ``` code fences entirely
- Skip content inside `...` backtick code spans
- Handle multi-line display math: $$ ... \\]  (opening $$, closing \\])
- Handle single-line display: $$ ... $$
- Handle inline $ blocks ($ ... $)
"""
import re
from pathlib import Path

DOCS_DIR = Path("/mnt/ramdisk/vanilla-wiki/docs/math/latex-math")


def _replace_display_inline(text: str) -> tuple[str, int]:
    """Replace $$ ... $$ with \\[...\\] in plain text (not inside backticks)."""
    out = []
    i = 0
    count = 0
    while i < len(text):
        if (i + 1 < len(text) and text[i] == '$' and text[i + 1] == '$'
                and (i + 2 >= len(text) or text[i + 2] != '$')):
            # Opening $$
            start = i + 2
            j = start
            found_close = False
            while j < len(text):
                if text[j] == '\\' and j + 1 < len(text):
                    j += 2
                    continue
                if j + 1 < len(text) and text[j] == '$' and text[j + 1] == '$':
                    out.append('\\[' + text[start:j] + '\\]')
                    i = j + 2
                    found_close = True
                    count += 1
                    break
                j += 1
            if not found_close:
                out.append('$$')
                i += 2
        elif text[i] == '$':
            out.append('$')
            i += 1
        else:
            out.append(text[i])
            i += 1
    return ''.join(out), count


def _replace_inline_dollar(text: str) -> tuple[str, int]:
    """Replace $...$ with \\(...\\) in plain text."""
    out = []
    i = 0
    count = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i + 1] == '$':
            out.append('\\$')
            i += 2
            continue
        if text[i] == '$':
            # Find matching closing $
            start = i + 1
            j = start
            depth = 1
            while j < len(text) and depth > 0:
                if text[j] == '\\' and j + 1 < len(text):
                    j += 2
                    continue
                if text[j] == '$':
                    depth -= 1
                    if depth == 0:
                        out.append('\\(' + text[start:j] + '\\)')
                        i = j + 1
                        count += 1
                        break
                    j += 1
                else:
                    j += 1
            else:
                out.append('$')
                i += 1
        else:
            out.append(text[i])
            i += 1
    return ''.join(out), count


def convert_line(line: str) -> tuple[str, int, int]:
    """Convert a single line (not inside code fence or display block).

    Returns (converted_line, inline_count, display_count).
    """
    # Split by backticks, only process non-code-span parts
    parts = line.split('`')
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            result.append(part)  # backtick code span — leave unchanged
        else:
            part, dc = _replace_display_inline(part)
            part, ic = _replace_inline_dollar(part)
            result.append(part)
    converted = ''.join(result)
    return converted, ic, dc


def convert_file(filepath: Path, dry_run: bool = True) -> tuple[int, int]:
    """Convert $$ and $ delimiters in a markdown file.

    Handles the $$ ... \\] format (opening $$, closing \\]) used in these files.
    """
    content = filepath.read_text(encoding='utf-8')
    lines = content.split('\n')
    total_inline = 0
    total_display = 0
    in_code_fence = False
    in_display = False
    display_start = -1
    new_lines = []

    for line_idx, line in enumerate(lines):
        stripped = line.strip()

        # Toggle code fence state
        fence_match = re.match(r'^\s*```', stripped)
        if fence_match:
            in_code_fence = not in_code_fence
            new_lines.append(line)
            continue

        # Skip everything inside code fences
        if in_code_fence:
            new_lines.append(line)
            continue

        # Track multi-line display math: $$ ... \\]
        if in_display:
            # Check if line contains \\] (with optional backslash escaping handled)
            if re.search(r'\\\][ \t]*$', stripped):
                # End of display block — replace \\] with \\]
                # We need to replace the opening $$ on the first line and \\] on this line
                new_lines[-1] = '\\[' + new_lines[-1].split('\n')[0].lstrip()
                # Replace this line's \\] with \\]
                converted_line = re.sub(r'\\\][ \t]*$', '\\]', stripped)
                # Preserve indentation
                if line != stripped:
                    converted_line = line[:len(line) - len(stripped)] + converted_line
                new_lines.append(converted_line)
                in_display = False
                total_display += 1
            else:
                # Middle of display block — leave as is
                new_lines.append(line)
            continue

        # Check for opening $$ (multi-line display block)
        if re.match(r'^\s*\$\$', stripped):
            # Check if this is a single-line $$ ... $$ (content between $$ on same line)
            # Single-line: $$content$$  — not a newline between $$
            inner = stripped[2:]
            if inner.endswith('$$'):
                # Single-line: $$...$$
                single, dc = _replace_display_inline(line)
                single, ic = _replace_inline_dollar(single)
                total_inline += ic
                total_display += dc
                new_lines.append(single)
            else:
                # Multi-line: $$ starts a block, closing with \\]
                in_display = True
                display_start = len(new_lines)
                new_lines.append(line)
            continue

        # Convert inline $$ and $ on regular lines
        converted, ic, dc = convert_line(line)
        total_inline += ic
        total_display += dc
        new_lines.append(converted)

    result = '\n'.join(new_lines)

    if result != content:
        if not dry_run:
            filepath.write_text(result, encoding='utf-8')
            print(f'  Modified: {filepath.name}')
        else:
            print(f'  [DRY RUN] Would modify: {filepath.name}')
    else:
        print(f'  [OK]: {filepath.name}')

    return total_inline, total_display


def main():
    files = sorted(DOCS_DIR.rglob('*.md'))
    if not files:
        print('No .md files found.')
        return

    print('=== DRY RUN ===')
    total_inline = 0
    total_display = 0
    for f in files:
        inline, display = convert_file(f, dry_run=True)
        total_inline += inline
        total_display += display

    print(f'\nSummary: {total_inline} inline, {total_display} display conversions')

    print('\n=== APPLYING ===')
    total_inline = 0
    total_display = 0
    for f in files:
        inline, display = convert_file(f, dry_run=False)
        total_inline += inline
        total_display += display
    print(f'\nApplied: {total_inline} inline, {total_display} display conversions')


if __name__ == '__main__':
    main()
