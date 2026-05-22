"""Built-in skills shipped with DeskPet.

Each subdirectory holds a ``SKILL.md`` bundle. The installer seeds
``<user_data>/deskpet/skills/built-in/`` from this package on first run;
runtime loads them via :class:`SkillLoader`.

Original P4-S10 set:

* ``recall-yesterday/`` — memory recall of yesterday's highlights
* ``summarize-day/``    — summary of today's conversation
* ``weather-report/``   — fetch + narrate current weather

P4 productivity skills:

* ``ppt-generate/``     — turn a topic / report into a .pptx
* ``deep-research/``    — multi-stage research with citations

Beta-100 pre-installed office suite (2026-05-22):

* ``excel-generate/``   — generate .xlsx (formulas / charts / styles)
* ``doc-edit/``         — create OR edit existing .docx documents
* ``pdf-export/``       — export documents to PDF via LibreOffice
* ``file-organize/``    — tidy a folder (by type / date / dedup)
* ``translate-doc/``    — translate text or a whole .docx
* ``web-read/``         — lightweight single-page article summary
* ``screenshot-ocr/``   — extract text from an image / screenshot
"""
