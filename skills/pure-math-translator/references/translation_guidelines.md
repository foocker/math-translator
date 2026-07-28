# Pure Mathematics Translation Guidelines

## Core Principles

1. Preserve mathematical meaning before style.
2. Do not weaken hypotheses, quantifiers, negations, uniqueness/existence statements, or dependencies between results.
3. Keep formulas, labels, references, citation keys, file paths, macro names, and code unchanged.
4. Preserve theorem/proof/definition/lemma/corollary/proposition/example/remark/exercise structure.
5. Use consistent terminology across the whole document, especially in long books or multi-file TeX projects.
6. Prefer established mathematical Chinese over literal translation.

## Translate

- Titles, chapter titles, section titles, theorem titles.
- Paragraph prose in papers, books, notes, examples, and exercises.
- Abstracts, introductions, prefaces, remarks, historical notes.
- Captions and visible table headers when they are descriptive.
- Footnotes and endnotes intended for readers.
- The visible names of theorem-like environments when configured in TeX.

## Do Not Translate

- Math formulas and equation environments.
- Macro names, environment names, labels, citation keys, file paths.
- `\input`, `\include`, `\includegraphics`, bibliography paths.
- Code blocks, pseudocode bodies, terminal output, raw data.
- Bibliography entries unless explicitly requested.
- Author names except standard Chinese names.

## Pure Math Style

- `Theorem` -> `定理`
- `Lemma` -> `引理`
- `Proposition` -> `命题`
- `Corollary` -> `推论`
- `Definition` -> `定义`
- `Proof` -> `证明`
- `Remark` -> `注`
- `Example` -> `例`
- `Exercise` -> `习题`

Use precise logical language:

- `if and only if` -> `当且仅当`
- `there exists a unique` -> `存在唯一的`
- `for all sufficiently large` -> `对所有充分大的`
- `without loss of generality` -> `不失一般性`
- `it remains to show` -> `还需证明`

Avoid proof-breaking paraphrases. In proofs, keep connectors such as `therefore`, `hence`, `conversely`, `by induction`, `by contradiction`, and `it follows from` explicit enough in Chinese.

## Ambiguous Terms

Do not blindly translate short ordinary words:

- `field` may be `域`, `场`, or ordinary `领域`.
- `ring` may be `环` or an ordinary object.
- `normal` may be `正规`, `法`, `正态`, or ordinary `正常`.
- `scheme` may be `概形` in algebraic geometry, but `方案` elsewhere.
- `map` may be `映射`, not always `地图`.

Use the domain, notation, and surrounding definitions to choose.

## Books And Lecture Notes

For books and lecture notes:

- Preserve chapter and section numbering.
- Translate exercises, hints, warnings, and notes as reader-facing text.
- Keep cross-references stable.
- If a chapter uses a term before defining it, preserve the author's exposition order.
- Prefer consistency with earlier chapters over local stylistic variation.
