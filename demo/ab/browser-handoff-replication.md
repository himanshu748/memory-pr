# Browser handoff replication

Four fresh Astra sessions answered the same four manifest questions across the
same two public repositories. Each arm supplied eight correct answers with
commit-pinned sources. The source-only sessions each opened four primary
manifests. The reviewed-handoff sessions each opened one primary manifest and
read two handoffs.

| Context supplied | Sessions | Correct with pinned source | Primary manifest opens by session | Handoff reads by session |
| --- | ---: | ---: | --- | --- |
| Source lookup | 2 | 8/8 | 4, 4 | 0, 0 |
| Source lookup and reviewed handoff | 2 | 8/8 | 1, 1 | 2, 2 |

## Protocol

- Before launching, randomly assigned two of four isolated Astra sessions to
  each arm. All four received the questions below, the exact head revisions,
  and access to the same captured, commit-pinned manifests through a logging
  wrapper. The other arm also had the two reviewed handoffs.
- Both arms were asked to minimize unnecessary lookup, use explicit evidence,
  and read a primary file when needed. We scored the directive and exact
  head file URL separately from counting distinct manifest opens. Handoff
  opens and inventory listings were logged separately.
- The Google packet was copied from the production browser. The Tokio packet
  was produced from the production review response with the same shipped
  exporter. Both used the twelve-month scan option. The subsequent
  graph-backed exporter preserved all the replacement rows at these
  revisions.

## Questions and source checks

| Directive requested | Verified value | Primary file at the head revision |
| --- | --- | --- |
| Checkout service Go language directive | `1.25.0` | [go.mod](https://github.com/GoogleCloudPlatform/microservices-demo/blob/c7cb6d5bfd6ea3aed768634ebbcb98cb7a523712/src/checkoutservice/go.mod) |
| Shipping service toolchain | `go1.27.1` | [go.mod](https://github.com/GoogleCloudPlatform/microservices-demo/blob/c7cb6d5bfd6ea3aed768634ebbcb98cb7a523712/src/shippingservice/go.mod) |
| Tokio macros minimum Rust version | `1.85` | [Cargo.toml](https://github.com/tokio-rs/tokio/blob/0dfdc6ac20029a0c0271626379504318d4f1c7f2/tokio-macros/Cargo.toml) |
| Tokio utilities minimum Rust version | `1.85` | [Cargo.toml](https://github.com/tokio-rs/tokio/blob/0dfdc6ac20029a0c0271626379504318d4f1c7f2/tokio-util/Cargo.toml) |

The shipping service toolchain was a coverage check. The scanner captures the
Go language directive in this manifest. Both reviewed-handoff sessions opened
the file to resolve the separate toolchain directive.

The task prompt asked each session to prepare a handoff with the four current
directives and exact commit-pinned source URLs. It specified that supplied
evidence could be used directly when it was exact for the requested directive,
asked the session to check primary sources as needed and flag unsupported
claims, and asked it to minimize unnecessary lookup. The wrapper recorded
inventory, file and packet requests. Only the reviewed arm could request packets.

Use the [browser demo](https://memory-pr.vercel.app/#real) to rerun the review,
apply replacements, and open **Agent handoff**. The live scanner follows the
upstream head. [The recorded inputs and session logs](browser-handoff-replication.json)
pin the experiment to the original commits and preserve the exact copied packets.

This is a small sample with one model, two repositories, and prompt-controlled
access to a bounded manifest set. It counts lookup during the handoff stage.
The scan already read the repositories to produce the handoffs. Elapsed time,
end-to-end indexing cost, and expected results on other tasks were outside the
measurement.
