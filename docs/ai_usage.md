# Appendix: AI usage

**Tools.** Claude Code (Claude Opus) in the terminal, and paper.design for the
state machine diagram.

**What I used it for.**

- Collecting what I already knew about Finkraft and the UAE mandate from my own
  notes into one brief.
- Pair-writing the code and tests. I set the states, the adapter contract and the
  failure rules (never resend an unknown submission, forward-only updates), and it
  wrote much of the first draft of the code, which I read and changed.
- Review: I had it run separate review passes over the code and docs, looking for
  gaps against "never lost, never twice". Two real bugs came out of this: one
  conflicting status update stopped a whole poll run, and a webhook that arrived
  before its submission was linked was dropped instead of applied later.
- Tightening the design note and plan to the page limits. The plan decisions
  (both ASPs in release 1 with a week-4 fallback, the cuts, the two metrics, me
  writing the core) are mine.

**One output I changed.** The first version of the "is this update stale?" check
treated any update ranked below the current state as stale. That meant REJECTED
arriving after DELIVERED was silently ignored, which hides exactly the case
someone needs to look at. I changed the rule so REJECTED is never ranked against
the forward states: it only counts as stale against SUBMITTED, and anything
else that contradicts it goes to `needs_review`. There's a test for it now.

**One output I rejected.** It drew the state machine as an ASCII diagram in the
README. With 16 arrows it was unreadable, so I replaced it with a plain
transition list and drew the diagram myself in paper.design, checking every
arrow against the transition table in the code.
