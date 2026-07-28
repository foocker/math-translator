# Chinese Support For TeX

Add CJK support before `\begin{document}` when compiling translated TeX with XeLaTeX.

## Basic Setup

```latex
\usepackage{xeCJK}
\setCJKmainfont{FandolSong}[ItalicFont=FandolKai]
\setCJKsansfont{FandolHei}
\setCJKmonofont{FandolFang}
```

If compiling locally on macOS or Windows, choose installed Chinese fonts instead of Fandol.

## Labels

```latex
\renewcommand{\figurename}{图}
\renewcommand{\tablename}{表}
\renewcommand{\abstractname}{摘要}
\renewcommand{\refname}{参考文献}
\renewcommand{\bibname}{参考文献}
\renewcommand{\contentsname}{目录}
```

## Theorem Names

Translate visible theorem names in `\newtheorem` declarations where appropriate:

```latex
\newtheorem{theorem}{定理}
\newtheorem{lemma}{引理}
\newtheorem{proposition}{命题}
\newtheorem{corollary}{推论}
\newtheorem{definition}{定义}
\newtheorem{remark}{注}
\newtheorem{example}{例}
\newtheorem{exercise}{习题}
```

If the document uses `amsthm`, keep `proof` as an environment name but localize the visible proof label:

```latex
\renewcommand{\proofname}{证明}
```

## Common Fixes

- Remove `\usepackage[T1]{fontenc}` if it conflicts with XeLaTeX Unicode fonts.
- Add `\raggedbottom` if mixed CJK/math content causes awkward vertical stretching.
- Insert `{}` between custom macros and following Chinese text, e.g. `\mathcal{F}` does not need it, but `\Spec{}是` may.
- Keep English quotation commands ``...'' or use Chinese quotes “...”; do not leave unmatched straight quotes.
