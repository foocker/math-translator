#!/usr/bin/env bash

set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

output_dir="${1:-output}"
domain="${2:-abstract_algebra}"
workers="${MATH_TRANSLATOR_WORKERS:-3}"
book_source="$output_dir/book.tex"
book_output="$output_dir/book-zh.tex"
chapters_dir="$output_dir/chapters"

if [ ! -f "$book_source" ]; then
  echo "缺少主文件：$book_source" >&2
  echo "请先运行 ocr_to_tex_agent.py --phase assemble。" >&2
  exit 1
fi

if [ ! -d "$chapters_dir" ]; then
  echo "缺少章节目录：$chapters_dir" >&2
  exit 1
fi

if [ ! -f "$book_output" ]; then
  sed \
    -e "s#\\\\title{Abel's Theorem in Problems and Solutions}#\\\\title{问题与解答中的阿贝尔定理}#" \
    -E -e 's#\\input\{chapters/([^}]+)\}#\\input{chapters/\1-zh}#g' \
    "$book_source" > "$book_output"
  echo "已创建中文入口：$book_output"
fi

find "$chapters_dir" \
  -maxdepth 1 \
  -type f \
  -name '*.tex' \
  ! -name '*-zh.tex' \
  -print0 |
xargs -0 -r -P "$workers" -I {} bash -c '
  file="$1"
  domain="$2"
  output_file="${file%.tex}-zh.tex"

  if [ -f "$output_file" ]; then
    echo "跳过已有文件：$output_file"
    exit 0
  fi

  echo "开始翻译：$file"
  python scripts/math_translate.py "$file" \
    -o "$output_file" \
    --document-kind chapter \
    --domain "$domain" \
    --reasoning medium \
    --no-learn
' _ "{}" "$domain"

missing=0
while IFS= read -r -d '' file; do

  output_file="${file%.tex}-zh.tex"
  if [ ! -f "$output_file" ]; then
    echo "缺少翻译章节：$output_file" >&2
    missing=1
  fi
done < <(
  find "$chapters_dir" \
    -maxdepth 1 \
    -type f \
    -name '*.tex' \
    ! -name '*-zh.tex' \
    -print0
)

if [ "$missing" -ne 0 ]; then
  echo "中文入口已存在，但章节尚未全部完成，暂时不能编译。" >&2
  exit 1
fi

echo "中文 TeX 已就绪：$book_output"
