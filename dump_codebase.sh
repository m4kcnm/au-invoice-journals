#!/usr/bin/env bash
OUTPUT="codebase_dump.txt"
> "$OUTPUT"

echo "AU INVOICE JOURNALS CODEBASE DUMP" >> "$OUTPUT"
echo "Generated on: $(date)" >> "$OUTPUT"
echo "========================================" >> "$OUTPUT"
echo "" >> "$OUTPUT"

# Find tracked python, html, js, css, and config files
find app -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) | sort | while read -r file; do
    echo "========================================" >> "$OUTPUT"
    echo "FILE: $file" >> "$OUTPUT"
    echo "========================================" >> "$OUTPUT"
    cat "$file" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done

# Include top-level runners and config manifests
for rootfile in run.py start.sh requirements.txt; do
    if [ -f "$rootfile" ]; then
        echo "========================================" >> "$OUTPUT"
        echo "FILE: $rootfile" >> "$OUTPUT"
        echo "========================================" >> "$OUTPUT"
        cat "$rootfile" >> "$OUTPUT"
        echo "" >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    fi
done

echo "Done! Codebase dumped to $OUTPUT"
