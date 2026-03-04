#!/bin/bash
# Count core agent lines (excluding channels/, cli/, providers/ adapters)
# Counts only actual code: excludes comments, docstrings, and blank lines

cd "$(dirname "$0")" || exit 1

echo "hermitcrab core agent line count"
echo "================================"
echo ""

# Function to count code lines (excluding comments, docstrings, blanks)
count_code_lines() {
    local file="$1"
    python3 -c "
import re
import sys

code_lines = 0
in_docstring = False
docstring_char = None

with open('$file', 'r', encoding='utf-8') as f:
    for line in f:
        stripped = line.strip()
        
        # Skip blank lines
        if not stripped:
            continue
        
        # Handle docstrings
        if in_docstring:
            if docstring_char in stripped:
                in_docstring = False
            continue
        
        # Check for docstring start
        if stripped.startswith('\"\"\"') or stripped.startswith(\"'''\"):
            docstring_char = stripped[:3]
            # Check if docstring ends on same line
            if stripped.count(docstring_char) >= 2:
                continue  # Single-line docstring, skip
            in_docstring = True
            continue
        
        # Skip comment-only lines
        if stripped.startswith('#'):
            continue
        
        # Count as code
        code_lines += 1

print(code_lines)
"
}

# Count lines for a directory
count_dir() {
    local dir="$1"
    local total=0
    
    for file in "$dir"/*.py; do
        if [ -f "$file" ]; then
            lines=$(count_code_lines "$file")
            total=$((total + lines))
        fi
    done
    
    echo "$total"
}

for dir in agent agent/tools bus config cron heartbeat session utils; do
    count=$(count_dir "hermitcrab/$dir")
    printf "  %-16s %5s lines\n" "$dir/" "$count"
done

root=$(python3 -c "
import sys
sys.path.insert(0, '.')
total = 0
for f in ['hermitcrab/__init__.py', 'hermitcrab/__main__.py']:
    with open(f, 'r') as file:
        exec(compile(open(f).read(), f, 'exec'), {'__name__': '__main__'})
" 2>/dev/null || echo 0)

# Simpler approach for root files
root=0
for file in hermitcrab/__init__.py hermitcrab/__main__.py; do
    if [ -f "$file" ]; then
        lines=$(count_code_lines "$file")
        root=$((root + lines))
    fi
done
printf "  %-16s %5s lines\n" "(root)" "$root"

echo ""

# Count total core lines
total=0
while IFS= read -r file; do
    if [ -f "$file" ]; then
        lines=$(count_code_lines "$file")
        total=$((total + lines))
    fi
done < <(find hermitcrab -name "*.py" ! -path "*/channels/*" ! -path "*/cli/*" ! -path "*/providers/*")

echo "  Core total:     $total lines"
echo ""
echo "  (excludes: channels/, cli/, providers/)"
echo "  (excludes: comments, docstrings, blank lines)"
