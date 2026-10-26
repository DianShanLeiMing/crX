# Desktop interface

Keep UI controls aligned with UIField, config.DEFAULTS and make_job_dictionary().
Persist only known keys. Deck checkboxes preserve selection order; cycle deck takes priority over random deck.
Device subprocess operations and storage scans run off Tk's main thread and return through queues.
Never update Tk widgets from background threads. All worker stats arrive through multiprocessing.Queue.
Disable configuration and recording deletion while the worker is running.
Preserve theme selection, local stats/logs and recording controls.
