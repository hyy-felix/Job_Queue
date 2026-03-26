# This module's logic lives in orchestrator.py (_run_generation, _resolve_output_folder,
# _validate_and_copy) because it's tightly coupled with state transitions.
#
# This file exists as a placeholder for potential future extraction if the
# generation logic grows complex enough to warrant separation.
#
# Subprocess contract:
#   Input:  claude --print -p "<prompt>" --working-directory <work_dir>
#   Stdout: redirected to generation.log (file handle, not PIPE)
#   Output: creates applications/MMDDYYYY/Company_Role/ with:
#           - resume.pdf, cover_letter.pdf (top-level)
#           - note/compile.log, note/selection_log.json, note/notes.md
#   Success signal: exit code 0 + OUTPUT_PATH: line in log + BUILD: SUCCESS in compile.log
#   Failure signal: non-zero exit code OR missing/empty PDFs OR missing BUILD: SUCCESS
