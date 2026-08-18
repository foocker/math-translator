#!/usr/bin/env bash

book_source="output/book.tex"
book_output="output/book-zh.tex"

if [ ! -f "$book_output" ]; then
  sed \
    -e "s#\\\\title{Abel's Theorem in Problems and Solutions}#\\\\title{问题与解答中的阿贝尔定理}#" \
    -e 's#\\input{chapters/\(chunk_[0-9][0-9]*\)}#\\input{chapters/\1-zh}#g' \
    "$book_source" > "$book_output"
  echo "已创建中文入口：$book_output"
fi

find output/chapters \
  -maxdepth 1 \
  -type f \
  -name 'chunk_*.tex' \
  ! -name '*-zh.tex' \
  -print0 |
xargs -0 -P 3 -I {} bash -c '
  file="$1"
  output_file="${file%.tex}-zh.tex"

  if [ -f "$output_file" ]; then
    echo "跳过已有文件：$output_file"
    exit 0
  fi

  echo "开始翻译：$file"
  python scripts/math_translate.py "$file" \
    -o "$output_file" \
    --document-kind chapter \
    --domain abstract_algebra \
    --reasoning medium \
    --no-learn
' _ "{}"

missing=0
for file in output/chapters/chunk_*.tex; do
  case "$file" in
    *-zh.tex) continue ;;
  esac

  output_file="${file%.tex}-zh.tex"
  if [ ! -f "$output_file" ]; then
    echo "缺少翻译章节：$output_file" >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "中文入口已存在，但章节尚未全部完成，暂时不能编译。" >&2
  exit 1
fi

echo "中文 TeX 已就绪：$book_output"
