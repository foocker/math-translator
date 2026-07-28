# Pure Math Translator

面向纯数学论文、讲义、书籍章节的英译中 agent。它吸收 `arxiv-paper-translator` 的成熟流程：先抽取上下文和术语，再翻译，之后强制审校；同时内置公式/代码保护、结构化输出、正式术语库和补充英汉数学词表。

## 支持输入

- 本地 Markdown：`.md`、`.markdown`
- 本地 LaTeX 单文件：`.tex`、`.ltx`
- 本地 LaTeX 工程：主文件加章节文件、图片、bib、sty/cls
- arXiv ID 或 URL：自动下载 e-print TeX 源码包后按 LaTeX 工程处理
- 数学书籍或讲义章节：优先保持章节、定理、证明、习题结构

## 快速调用脚本

单文件翻译可直接使用脚本：

```powershell
python tools/pure-math-translator/scripts/math_translate.py chapter1.tex --document-kind book --domain topology
python tools/pure-math-translator/scripts/math_translate.py paper.md --document-kind paper --domain algebraic_geometry
```

arXiv 地址先用下载脚本取源码：

```powershell
python tools/pure-math-translator/scripts/fetch_arxiv_source.py "https://arxiv.org/abs/2206.04655"
```

脚本会生成 `arXiv_<ID>/paper_source/`，之后按 LaTeX 工程翻译。

脚本默认加载内置词典，并把命中的术语作为翻译强约束交给模型：

- `tools/pure-math-translator/glossary.json`
- `tools/pure-math-translator/dictionaries/english_chinese_math_terms.tsv`

TSV 词表以保守方式作为补充源，正式 JSON 词典优先。大词典不会整体塞进模型上下文：脚本会先在待翻译文件中检索命中项，只把当前文档/当前分块命中的术语注入提示。翻译长文前建议先跑术语抽取或 dry-run 检查命中：

```powershell
python tools/pure-math-translator/scripts/extract_terms.py chapter1.tex --document-kind book --domain topology -o chapter1.terms.md
```

```powershell
python tools/pure-math-translator/scripts/math_translate.py chapter1.tex --document-kind book --domain topology --dry-run
```

## Skill

Agent 说明位于：

```text
tools/pure-math-translator/skills/pure-math-translator/SKILL.md
```

将该 skill 安装到支持 Agent Skills 的环境后，可用于较长的 TeX 工程、arXiv 源码翻译、书籍章节翻译和译后审校。
