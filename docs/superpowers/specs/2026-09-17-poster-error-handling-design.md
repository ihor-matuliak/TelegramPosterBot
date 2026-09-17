# Autoposting pause correction

Approved scope: a restriction in one group disables that group and must not
stop healthy destinations. Keep account protections and explain waits in the UI.

Posting returns one of four explicit outcomes: sent, chat unavailable, deferred,
or system error. Only system errors increment the circuit breaker; success and
an explicit pause/resume reset its counter. Three system errors pause posting.
The local worker stays paused if saving that emergency pause fails.

SlowMode postpones only its chat. FloodWait delays the worker for Telegram's
required duration plus a margin. Native-message sending must propagate these
errors without immediately retrying as plain text. Existing random delays remain;
daily limits and batch boundaries are also checked during a fetched batch.

The existing logs table records pause/resume events and the emergency cause,
so the pause explanation survives restart without a schema migration. Status
includes local worker liveness, authorization, limits and timed waits. The UI
displays these blockers and labels eligible chats as queued rather than promising
immediate delivery. Mobile controls report failed state writes and reset errors.

Implementation and verification: update worker classification, control-event
persistence, API and both controls, then run isolated Python and JavaScript tests.
Regression: three restricted groups followed by a healthy group must send only
to the healthy one and leave the master switch enabled. Also cover three system
errors, reset, SlowMode, FloodWait, native-message failures, status after restart,
failed persistence, limits, and paused/unknown frontend state. All Telegram and
database calls in these checks are mocked. No live sending or automatic resume.
