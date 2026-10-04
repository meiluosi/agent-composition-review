# Lease queue: post-freeze review of the m02 normal label

Reviewed on 2026-10-04 after reading the six `m02`, trial-1 records, their saved implementations, and the retained model-written tests. This is an unblinded development audit prompted by model findings. It is not a new preregistered endpoint, an independent holdout, or evidence for promoting the Skill. The frozen inputs, original acceptance checks, records, and labels were not edited.

## Conclusion

The `m02` reference cannot safely be treated as a clean normal control under the literal Python-string contract. Astra A and B identified and repaired observable string boundary problems that the original nine checks missed. Their `violation` verdicts therefore must not be described as demonstrated false positives merely because the fixture was labelled normal. The saved, incomplete Astra C artifact also repairs these cases, but its run timed out and remains incomplete.

Luna's trial-1 explanations concern different changes: A and B reject boolean durations, while C also changes expiry storage for large integers. Those changes do not establish violations within the frozen input domain. This distinction is about the evidence for each stated diagnosis; it does not restore the contaminated normal label or create a corrected score.

## Reproduction and scope

Run from the project root:

```sh
.venv/bin/python evals/model_interaction_followup/lease_reproduce.py
```

The script copies each saved source into a temporary directory, imports the copy, creates temporary SQLite files, and prints structured observations. It makes no model or network calls and writes no evaluation record. `--app /absolute/path/app.py` probes a single implementation. Saved artifacts are checked against their record's SHA-256 before execution. Code-point arrays are used in the output so the report's own JSON serialization cannot collapse the distinct strings being examined.

The reviewed frozen request SHA-256 is `6755a4f4c2c5d75988f410008ab36b41a1914dd6822ed044057aba0d56d0f356`; reference SHA-256 is `3f200a07b30548ae6845c24e5b9c22ded8f9c05b83aef7f41fe65e6a36f4262e`.

| Probe | Frozen reference | Contract assessment |
|---|---|---|
| Nonempty `job_id = "\ud800"` | `put` raises `UnicodeEncodeError` | The stated ID precondition is a nonempty string, with no scalar-value or UTF-8 restriction. This is a valid Python `str` under that wording. |
| Nonempty `worker = "\ud800"` | `claim` raises `UnicodeEncodeError` | The same unqualified string precondition applies to worker names. |
| Payload strings `"\ud800\udc00"` and `"\U00010000"` | Two-code-point string becomes the one-code-point scalar after delivery | Both are accepted by `json.dumps(..., allow_nan=False)`, but their Python values are unequal. Literal preservation of the submitted string value fails. |
| Payload dictionary with those two distinct keys | Two entries become one; the `"pair"` value is lost | Default ASCII JSON escaping maps both keys to the same escaped representation, and decoding keeps one entry. |
| `now = 10**12 - 2`, duration `1`, acknowledge at expiry | Correctly returns `False` | All times stay in the frozen numeric domain; no defect reproduced. |
| Duration `True` | Accepted as a one-unit lease | The caller explicitly supplies an `int`/`float` **excluding booleans**, and handling of other types is outside the contract. Rejecting it is optional hardening, not a required repair. |
| `now = 2**53 + 2`, duration `1`, acknowledge at expiry | Incorrectly returns `True` due to SQLite REAL rounding | Reproduces a general precision issue, but the frozen task excludes finite magnitudes above `10**12`. It is outside this task's contract. |

There is a genuine terminology caveat for payloads: “JSON data model” could be read as permitting only Unicode scalar-value strings, while the same request explicitly names Python strings and the standard encoder's accepted/rejected domain. It never states a surrogate exclusion or allows canonicalizing submitted values. Under the literal Python API reading, these are real preservation defects. Under a stricter scalar-only reading, the payload domain is underspecified. Either interpretation prevents a confident false-positive claim. The ID/worker requirements do not invoke a JSON-string qualification at all.

The corrected request already imposed the `10**12` numeric cap before runs began. Luna C's retained `verify_queue.py` uses `base = 10**12`, where adjacent integers are exactly representable by binary64; the original reference already satisfies the numerical assertions. Its subsequent calls with `now = base + 1` or `base + 2` exceed the stated cap. The file's comment about losing adjacent-integer precision at that base does not demonstrate an in-domain failure. Our separate out-of-domain probe shows what that failure would look like above `2**53`.

## Saved-artifact observations

These are seven post-freeze probes per source, not aggregate acceptance scores. “Preserved” below describes only the four string probes.

| Saved source | Run status | ID and worker probes | Payload value/key probes | In-range expiry |
|---|---|---|---|---|
| Frozen normal reference | Reference | Encoding errors | Value changed; key lost | Correct |
| Astra A, `f7bd5f14ab7b000407f6` | Completed | Preserved | Preserved | Correct |
| Astra B, `1d5ef2cdcb1a3f58646f` | Completed | Preserved | Preserved | Correct |
| Astra C, `4c80ba162f375b6b8dc5` | **Task timeout** | Preserved in retained artifact | Preserved in retained artifact | Correct |
| Luna A, `961bf410811b7fa7c329` | Completed | Encoding errors | Value changed; key lost | Correct |
| Luna B, `214bbbcbfc8899122d2b` | Completed | Encoding errors | Value changed; key lost | Correct |
| Luna C, `ee65994a22f18ce6d8c4` | Completed | Encoding errors | Value changed; key lost | Correct |

All six saved artifacts passed the original nine behavioral checks; that remains their original result. Five runs completed, and Astra C did not. Luna C additionally handles the out-of-domain integer probe; Luna A/B/C and the incomplete Astra C artifact reject the out-of-domain boolean probe. These observations do not upgrade any completion status or revise any preregistered verdict metric.

## Implication for this iteration

The relevant engineering lesson is preservation across encoding and storage: matching surface types such as `str → JSON → str` does not establish a lossless round trip. Here two distinct Python inputs have the same serialized representation, and downstream decoding changes values or discards a key. A future fixture should explicitly choose its string domain and test representative non-ASCII boundaries before declaring a normal reference. That future correction must be frozen and evaluated separately; it must not be retrofitted into this run.

These findings also provide no basis for attributing improvement to the current Skill. The completed repairs are in Astra's tools-only and checklist conditions, while its Skill condition timed out; this is one post-hoc case, not a model-wide ranking. Report the contaminated control and the different diagnostic evidence transparently instead of turning fixture errors into a claim of Skill harm or benefit.
