# Math Translator

将 PaddleOCR-VL(如果它输出格式变了，这块得跟着改) 的数学书籍 OCR 结果转换为英文 TeX，保版面结构，再将英文 TeX 翻译为中文 TeX。

当前目录约定：

```text
test_resources/   输入测试 PDF
examples/         参考 PDF，不是运行时输入目录
output/           OCR、TeX 和翻译产物
templates/        ElegantBook 模板
config/           OCR 修复规则(进化用的，你自己用多了，沉淀一下)
scripts/          OCR、TeX 转换和翻译脚本
```

## 环境准备

在 `tools/math-translator` 目录执行命令。需要：

- Python
- `requests`：`pip install requests`
- Codex CLI，并已完成本机登录
- XeLaTeX（编译 PDF 时需要）

OCR API Token 放在项目根目录的 `.env`：

```powershell
Copy-Item .env.example .env
notepad .env
```

```env
PADDLEOCR_TOKEN=your-token
```

也可以直接设置环境变量 `PADDLEOCR_TOKEN`；环境变量优先于 `.env`。

## 一、获取 OCR Markdown

使用本地 PDF：

```powershell
python scripts/ppocr-vl.py "test_resources/Abel’s Theorem in Problems and SolutionsArnold.pdf" --ocr-only
```

使用 URL：

```powershell
python scripts/ppocr-vl.py "https://example.com/book.pdf" --ocr-only
```

`--ocr-only` 只调用 OCR，输出写入 `output/`：

```text
output/doc_0.md
output/doc_1.md
output/imgs/...
output/layout_...
```

如果希望 OCR 完成后自动继续英文 TeX Agent，可以省略 `--ocr-only`：

```powershell
python scripts/ppocr-vl.py "test_resources/Abel’s Theorem in Problems and SolutionsArnold.pdf"
```

原始 `doc_N.md` 不会被翻译或覆盖。OCR 脚本默认从脚本位置解析 `output/`，不依赖当前命令行目录。

## 二、OCR Markdown 转英文 TeX

如果 `output/doc_N.md` 已经存在，可以跳过 OCR，直接运行 Agent 转换。

完整流程（分类、转换、审校、组装）：

```powershell
python scripts/ocr_to_tex_agent.py --phase full
```

`--output-dir` 默认就是 `output`；只有处理其他 OCR 目录时才需要显式指定。

常用阶段：

```powershell
# 只分类页面
python scripts/ocr_to_tex_agent.py --phase classify

# 转换章节
python scripts/ocr_to_tex_agent.py --phase convert

# 审校章节
python scripts/ocr_to_tex_agent.py --phase review

# 组装英文根文件
python scripts/ocr_to_tex_agent.py --phase assemble
```

主要产物：

```text
output/agent-work/page_manifest.json
output/chapters/chunk_0000.tex
output/chapters/chunk_0001.tex
...
output/book.tex
```

如果 `output/` 中存在旧 OCR 页，使用 `--page-count N` 限定只处理 `doc_0.md` 到 `doc_<N-1>.md`：

```powershell
python scripts/ocr_to_tex_agent.py --phase full --page-count 202
```

默认启用无人值守 Agent 执行，不需要手动添加 `--dangerously-bypass-approvals-and-sandbox`。需要交互式审批时才使用：

```powershell
python scripts/ocr_to_tex_agent.py --phase full --safe-agent
```

重新生成已有章节或审校结果时才添加 `--overwrite`。正常续跑不要添加该参数。

## 三、英文 TeX 翻译为中文 TeX

这一步只读取已经生成的 `.tex`，不会重新读取 OCR Markdown，也不会修改英文源文件。

推荐在 Git Bash 中运行并发翻译：

```bash
bash translator.sh
```

脚本默认使用 3 路并发，跳过已经存在的中文章节，支持中断后续跑。输出为：

```text
output/chapters/chunk_0000-zh.tex
output/chapters/chunk_0001-zh.tex
...
output/book-zh.tex
```

当前 `translator.sh` 是 Abel 示例的实验配置，固定使用 `abstract_algebra` 领域并生成中文书名“问题与解答中的阿贝尔定理”。处理其他书籍前，需要调整脚本中的 `--domain` 和根文件标题映射。

单章测试可以直接运行：

```bash
python scripts/math_translate.py \
  output/chapters/chunk_0000.tex \
  -o output/chapters/chunk_0000-zh.tex \
  --document-kind chapter \
  --domain abstract_algebra \
  --reasoning medium \
  --no-learn
```

`共 N 个翻译分块，命中 M 处正式术语` 是本地分块和正则术语匹配，不调用模型；出现 `翻译 1/N` 后才开始调用 Codex。术语警告是软警告，不会阻止文件写入。

翻译脚本保护公式、LaTeX 命令、引用和图片路径，并在写文件前检查保护标记及非法控制字符。原始英文文件保持不变。

## 四、编译 PDF

从输出目录编译英文版：

```powershell
Set-Location output
xelatex -interaction=nonstopmode -halt-on-error book.tex
xelatex -interaction=nonstopmode -halt-on-error book.tex
```
我自己一般不用中间这些参数。

编译中文版：

```powershell
xelatex -interaction=nonstopmode -halt-on-error book-zh.tex
xelatex -interaction=nonstopmode -halt-on-error book-zh.tex
```

结果：

```text
output/book.pdf
output/book-zh.pdf
```

需要编译两遍以解析目录、交叉引用和图片引用。首轮出现 `Reference ... undefined` 通常是正常的；第二轮后仍存在才需要排查。

## 测试样本与参考产物

仓库自带测试输入：

```text
test_resources/Abel’s Theorem in Problems and SolutionsArnold.pdf
test_resources/与中学生谈谈代数.pdf
```

`examples/` 保存参考 PDF，例如：

```text
examples/Abel’s Theorem in Problems and Solutions.pdf
examples/book-zh.pdf
```

