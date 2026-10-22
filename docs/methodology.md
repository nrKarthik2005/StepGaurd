# Methodology

## Model

A workflow is a finite-state machine W = (Q, Σ, δ, q0, F). The environment state *s* is only partly observable;
an abstraction α maps an observation to a workflow stage using ordered `stage_rules`. Every tool has a contract:

* **Pre** — role, dependencies (`requires`), argument schema, and preconditions over the observed state, the
  arguments and the task;
* **Post** — output schema, effect conditions over (before, after), the expected stage δ(q, a), and the
  workflow invariants;
* **reversibility** — R (restorable from a snapshot), C (undone by a compensating action whose arguments are
  computed from the observed effect) or I (irreversible);
* **idempotency** — non-idempotent tools declare `key_fields`; the logical key `tool:field=value…` identifies a
  repeated operation;
* **unchanged** conditions used by the probe to recognise an effect that did not happen;
* **allowed recovery strategies**.

## Verdicts

| Verdict | Meaning | When |
|---|---|---|
| ACCEPT | the check passed | Pre and Post both hold |
| BLOCK | the action is not executed | a pre-condition fails (out of order, missing dependency, bad arguments, duplicate key, precondition false, state unobservable) |
| VIOLATE | the action ran and the state is wrong | output schema, effect, expected stage or invariant fails |
| INCONCLUSIVE | the action ran and the check could not decide | the post-state is unobservable or a condition is undefined (e.g. a timeout left no output) |

Every decision stores the step, action and arguments, stage before / expected / observed, verdict, failed
predicate, message, output or error, injected fault, probe result, admissible strategies, chosen strategy and
reason, checkpoint index, barrier, in-flight keys, restoration result, timestamps and verifier CPU time.

## Algorithm (runtime_controller.py)

1. Take the next proposal (or a replay injected by E5); pass it through the fault injector.
2. **Pre-check.** On BLOCK, return structured feedback (failed predicate, message, enabled actions, suggested
   fix, or the already-committed result for duplicates) to the agent.
3. Execute with the action key recorded in the in-flight log P.
4. **Post-check** on the observed post-state.
5. On ACCEPT: commit — update stage and history, push a checkpoint, and move the barrier after an irreversible
   action.
6. Otherwise: take a consistent read, run the **effect probe**, check whether RESUME's conditions hold and
   whether rollback is feasible, compute the admissible set and choose a strategy:
   * **RESUME** — only if the probe says APPLIED and Post without the schema check holds on a consistent read;
     lost output fields are rebuilt from state (`resume_output`).
   * **RETRY** — only if the tool is idempotent or the probe says ABSENT, within the retry budget.
   * **ROLLBACK** — only if checkpoints are enabled, the target is not behind the barrier and every action since
     the checkpoint is R or C; restoration is verified and a failed restoration escalates.
   * **REPAIR** — for tool errors whose effect is ABSENT: the error and hint go back to the agent.
   * **ESCALATE** — always admissible; ends the episode safely.
7. Budgets stop runaway episodes (executions, blocks, proposals, retries).

The selective policy orders strategies by failure class (inconclusive, output schema, effect, absent effect,
invariant, tool error). The unselective ablation tries RETRY (or REPAIR for tool errors) and otherwise escalates.
The admissibility rules are kept in every configuration, so ablations never run an unsafe retry.

## Faults (fault_injector.py)

| Fault | Injection | Hook |
|---|---|---|
| E1 out-of-order | a proposal is replaced by a later step's action | propose |
| E2 missing prerequisite | the environment loses a prerequisite (expired session, deleted input) just before a step | propose |
| E3 malformed output | a required output field is dropped; the effect still happens | execute |
| E4 silent failure | the tool reports success but the effect is suppressed, corrupted, or subtly wrong | execute |
| E5 duplicate | an irreversible action is replayed after a lost acknowledgement | replay |
| E6 timeout | the call times out before reaching the service or after the effect | execute |

Occurrences, variants and task parameters come from RNG streams derived from (workflow, template, seed,
condition) only, so every configuration faces exactly the same fault at the same point. An occurrence counts only
calls the service accepts, so a call that fails on its own does not use up the fault.

Some E4 "subtle" variants are deliberately outside what the contracts express (a wrong action-item count in the
document workflow; two swapped revenue figures in the data pipeline). They measure the limit of
contract-based checking.

## Agent

The default agent is a seeded rule-based simulator that follows the workflow's reference plan, with a small
probability of skipping a step (`p_skip` 0.04) or repeating one (`p_dup` 0.02), and a probability `p_fix` 0.5
of correcting itself when it sees an error hint. These rates are modelling assumptions, not measurements of
any LLM. The optional `LLMAgent` proposes JSON actions through an OpenAI-compatible endpoint and is subject to
the same checks; it is not used in the reported experiments.

## Baselines and ablations

* **A_UNGUARDED** — the agent calls tools directly.
* **B_INTERCEPTOR** — a realistic tool-call interceptor: argument-schema and role validation before the call,
  and retry with exponential back-off on retryable errors and timeouts (as generic middleware does). It has no
  workflow model, no state checks and no checkpoints. It was not weakened: it retries timeouts exactly as common
  middleware would, which is also why it can duplicate a non-idempotent call.
* **SG_FULL** and four ablations: **SG_NO_PRE** (no pre-checks), **SG_NO_POST** (no post-checks: accepted
  without verification), **SG_NO_CHECKPOINT** (no checkpoints, so no rollback), **SG_UNSELECTIVE_RECOVERY**.
  SG_NO_POST keeps the pre-check's in-flight log, but with no post-check there is no effect probe to resolve a
  timed-out non-idempotent call. Its key therefore stays in flight and later attempts are blocked: this prevents
  duplicate charges but also costs completion when the timeout happened before the effect. This is why
  SG_NO_POST can complete fewer E6 tasks than the unguarded agent.

## Oracle

`oracle.py` is written separately from the contracts. It reads the environment's unrestricted truth and ledger
and decides, per step, whether the proposed action was unsafe (unknown tool, undefined transition at the true stage,
invalid arguments, a missing prerequisite in the true state such as an expired session or a cart that does not
match the request, or a duplicate irreversible effect), whether the goal was met at the end, and how many irreversible side
effects were duplicated. Metrics use the oracle, not StepGuard's own verdicts.

## Metrics

| Metric | Definition |
|---|---|
| TCR (clean / fault) | episodes whose goal the oracle confirms, over episodes in the condition |
| PER | unsafe actions executed / actions executed |
| EDR | fired faults followed, within two steps, by a BLOCK, VIOLATE or INCONCLUSIVE verdict (for B: a BLOCK or a RETRY) / fired faults |
| RSR | detected faults after which the task completed without escalation / detected faults |
| UAPR | unsafe proposals blocked / unsafe proposals |
| ETO | median relative virtual-time increase over the paired A episode on clean runs |
| ΔTC, ΔReads | mean extra tool executions / state reads over the paired A episode |
| FPRv | verifier flags on correct steps / correct steps (clean runs) |
| Escalation rate, duplicates, mean retries, recovery latency, verifier CPU per step | as named |

A ratio with a zero denominator is reported as *n/a*, never as 0 or 100%. EDR, RSR and FPRv are undefined for A.
ETO uses a virtual cost model (agent step 1.0 s, tool call 0.15 s, state read 0.05 s, snapshot 0.01 s, back-off
0.5 s) because the simulated tools are much faster than real ones; real verifier CPU time is reported separately.

## Statistics

* 95% Wilson intervals for completion rates.
* Paired comparisons of SG against every other configuration on the identical fault scenarios: difference in
  completion, 95% cluster-bootstrap CI (clusters = workflow × task template, 2000 resamples, fixed seed), exact
  McNemar test on discordant pairs, Holm adjustment across comparisons, Cohen's h.
* A run is labelled **confirmatory** only in `full` mode with workflow specifications unchanged between the start
  and the end of the run (SHA-256 check); everything else is **exploratory**.

## Threats to validity

The same author wrote the environments, the contracts, the faults and the oracle; the agent is a simulator; the
tasks are small and synthetic; fault rates are fixed by design rather than observed in deployments. Results show
how the mechanism behaves on this harness, not how it would perform with real LLM agents and real services.
