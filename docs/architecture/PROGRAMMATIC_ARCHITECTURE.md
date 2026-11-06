# Programmatic architecture

Visuara Programmatic is a future layer. Phase 13 and M17 deliver its contracts, its adapter interfaces, test adapters and a way to verify an adapter against the contracts. **No partner is connected, and no partner adapter exists in this repository.** `GET /api/programmatic/capabilities` returns the same states as this page, and its `external_connections` count is 0.

## Integration states
| State | Meaning |
|---|---|
| `DESIGNED_ONLY` | A design exists. No adapter interface is implemented |
| `NOT_CONNECTED` | The interface and its contract exist; no adapter is configured |
| `CONNECTED` | An adapter is configured and answers its health check |
| `VERIFIED` | A connected adapter passed the contract checks in the last 30 days |

Every integration also reports `external`: `true` only for a real partner's adapter. The built-in test adapters are always `external: false`, whatever their state.

## Capabilities today
| Capability | State | Contract | What exists |
|---|---|---|---|
| Inventory feed | IMPLEMENTED (our own endpoint) | `visuara-inventory-feed/1` | `GET /api/programmatic/inventory-feed` (publisher): the organization's AVAILABLE items, built from canonical inventory. Every item is checked against the contract before it is returned |
| Bid-request mapping | IMPLEMENTED | `visuara-inventory-feed/1` | `programmatic.to_bid_request()`: one item as an OpenRTB-style object. It isn't sent anywhere |
| Direct deals | IMPLEMENTED | — | Marketplace reservations: fixed price, one winner per item |
| Supply-side platform | NOT_CONNECTED | `visuara-inventory-feed/1` | `SupplyAdapter` (health, publish, withdraw); `LoopbackSupply` test adapter |
| Demand-side platform | NOT_CONNECTED | `visuara-deal/1` | `DemandAdapter` (health, fetch deals); `LoopbackDemand` test adapter |
| Third-party measurement | NOT_CONNECTED | `visuara-delivery-report/1` | `MeasurementAdapter` (health, import report); `LoopbackMeasurement` test adapter |
| Real-time bidding | DESIGNED_ONLY | — | No interface; bidding needs an exchange agreement first |
| Pacing and budget optimisation | DESIGNED_ONLY | — | Campaign budgets are enforced only when a request is made |
| Semantic scene search | DESIGNED_ONLY | — | Structured filters are implemented (AD-11); embeddings are not |

The contracts themselves are served at `GET /api/programmatic/contracts`. They're versioned (`/1`). A change that breaks a partner gets a new version, and the old one is kept while anything uses it.

## Verifying an adapter
- **Test adapters:** `CC_PROGRAMMATIC_TEST_ADAPTERS=1` (refused in production) enables the loopback adapters, so the whole path can be exercised.
- **Verification run:** a platform administrator runs `POST /api/admin/programmatic/verify`.
- **What it checks**, for each configured adapter:
  - its health check;
  - for supply: that it accepts a contract-valid feed and returns a reference;
  - for demand: that every deal it returns is contract-valid;
  - for measurement: that its report is contract-valid.
- **Results:** stored in `integration_checks`. An adapter that fails stays `CONNECTED` and shows the errors.
- **Tests:** `tests/test_programmatic.py` covers each state, a failing adapter, and the production refusal.

**To add a real partner:**
1. Implement its interface in a module of its own, with `external = True`.
2. Configure it from settings that hold its credentials.
3. Register it in `programmatic.adapters()`.
4. Run the verification against the partner's sandbox.
5. Mark nothing as live until the commercial agreement and the partner's certification exist.

## Design
- **One inventory:** programmatic channels sell the same `inventory_items` as the Marketplace. A sale through any channel must win the same conditional reservation (AD-8), so an item can't be sold twice across channels. Items whose rights have lapsed are not sellable (D-MKT-01) and must be withdrawn from any partner.
- **Adapters are plain interfaces.** A capability with no adapter uses `NotConnected`, which raises `NotImplementedError`, so no code path can silently act as connected.
- **Events:** a supply adapter would subscribe to `reservation.*` and `inventory` changes through the outbox ([EVENT_CONTRACTS.md](EVENT_CONTRACTS.md)) instead of polling.
- **Scores:** a bid request marks the score `score_is_heuristic: true`, so a buyer never takes it for a validated prediction.
- **Pricing:** the feed's `bidfloor` is the publisher's fixed price in major units. Dynamic floors would need a pricing model, which doesn't exist.

## Before any of this goes live
- Commercial agreements and a partner sandbox.
- Exchange certification.
- Measurement agreements.
- Legal review (B-8).
