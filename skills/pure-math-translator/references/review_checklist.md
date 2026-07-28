# Pure Mathematics Translation Review Checklist

## File Completeness

- All source `.tex`, `.md`, and included text files are present in the translated directory.
- Non-text assets are copied unchanged.
- Main/root file is translated.
- Included chapters/sections are translated or intentionally skipped with a note.

## Structure Preservation

- Markdown headings and lists remain structurally aligned.
- LaTeX `\part`, `\chapter`, `\section`, `\subsection` counts and order are preserved.
- Theorem-like environments remain balanced.
- Proofs are not merged into theorem statements or separated from their conclusions.

## Protected Content

- Formulas and equation environments are unchanged.
- `\label`, `\ref`, `\eqref`, `\cref`, `\cite` keys are unchanged.
- File paths in `\input`, `\include`, `\includegraphics`, bibliography commands are unchanged.
- Code, verbatim, minted, lstlisting, and tikz/pgf bodies are unchanged.

## LaTeX Safety

Find suspicious new commands:

```bash
diff <(cd source && grep -ohrIE '\\[a-zA-Z]+' | sort -u) \
     <(cd translated && grep -ohrIE '\\[a-zA-Z]+' | sort -u)
```

Find CJK directly attached to macros:

```bash
grep -rnE '\\[a-zA-Z]+[一-龥]' translated/ --include='*.tex'
```

Insert `{}` where needed, e.g. `\Spec{}是`.

## Mathematical Accuracy

- Definitions preserve iff/only if direction.
- Theorems preserve all hypotheses.
- Proofs preserve induction/contradiction/converse logic.
- Existence and uniqueness statements are not conflated.
- Negations and exceptions are preserved.
- Notation is not silently renamed.

## Terminology

- Shared terminology is consistent across files.
- Ambiguous terms are handled by context.
- Candidate terms are recorded for later review.
- Short words such as `field`, `ring`, `normal`, `scheme`, `map` are not blindly translated.

## Chinese Quality

- Chinese is fluent mathematical prose.
- Avoid overly literal English word order.
- Avoid unnecessary filler such as “来”“地”“的”“了” where it weakens style.
- Use “本文”“本章”“下文”“上式”“命题”等 according to document kind.
- Books and lecture notes may be slightly more explanatory; research papers should remain concise.
