"""Built-in skills shipped with DeskPet (P4-S10 task 15.7).

Three SKILL.md bundles live here:

* ``recall-yesterday/`` — memory recall of yesterday's highlights
* ``summarize-day/``    — summary of today's conversation
* ``weather-report/``   — fetch + narrate current weather

The installer seeds ``<user_data>/deskpet/skills/built-in/`` from this
package on first run; runtime loads via :class:`SkillLoader`.
"""
