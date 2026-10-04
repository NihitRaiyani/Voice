# Project and learning charter

Roma is a backend-only counselling/appointment system and a student engineering laboratory. A useful call is safe, interruptible, measurable and truthful about booking. Digital Marketing remains the active course; adding schema entities does not approve new course claims.

The selected [v4 roadmap](roadmaps/README.md) replaces the earlier curriculum. Progress uses Levels 0–14 and concrete acceptance gates. Current scope is documentation/design; implementation proceeds later one requested level at a time.

## Learning outcomes

Explain async HTTP/WebSocket/audio, deterministic conversation control, relational constraints/transactions, PostgreSQL versus Redis, provider dependency inversion, direct local model inference, multilingual ASR/TTS measurement, conditional grounded retrieval, idempotent jobs, security/privacy, telemetry and measured capacity.

Every increment needs working behavior, a protected invariant, meaningful verification and an explanation of the trade-off. Do not claim an installed dependency, schema table or diagram is a completed feature.

## Product boundaries

Keep Twilio/Pipecat and cloud providers as the comparison baseline. Understand Gujarati/Hindi/English/code-mix; live replies stay Hindi-base Hinglish, with separately evaluated multilingual output. Code controls stages, slots, safety, consent and hang-up. All generated speech is filtered before synthesis.

The final local profile uses local AI with zero external GenAI API calls. Carrier/internal model-service APIs are allowed. PostgreSQL owns durable appointments and business records; Redis handles transient work. RAG is conditional at Level 9, full Docker packaging begins at Level 13 and advanced work requires measured need.

Backend APIs are the product surface; no core frontend is required. Consent/certificate approval, deployment ownership, local-model licenses/hardware and final SLOs remain owner decisions. See [decisions](decisions.md), [completion contract](completion-contract.md) and [study guide](17-placement-study-guide.md).
