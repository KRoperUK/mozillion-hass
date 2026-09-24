## [0.5.2](https://github.com/KRoperUK/mozillion-hass/compare/dev-v0.5.1...dev-v0.5.2) (2026-07-09)


### Bug Fixes

* **coordinator:** transparently re-authenticate on expired sessions ([a3eafa2](https://github.com/KRoperUK/mozillion-hass/commit/a3eafa25900f4f5f93a5cfa9bd8b022e419f3798))

## [1.0.0](https://github.com/KRoperUK/mozillion-hass/compare/v0.6.1...v1.0.0) (2026-09-24)


### ⚠ BREAKING CHANGES

* entries written by the old contract are migrated on first start; the migration re-reads the SIM list to discover the sim_meta_id. The `usage_key`/`remaining_key` options are gone (the response fields are known now), and a SIM's unique id is its sim_meta_id rather than its order_detail_id.

### Features

* add wallet, plan and reset-date sensors ([#31](https://github.com/KRoperUK/mozillion-hass/issues/31)) ([e2bb0b9](https://github.com/KRoperUK/mozillion-hass/commit/e2bb0b990dcf144933b6d3bc7ae93e1dcdbd234c))
* add Welsh, Irish and French translations ([#29](https://github.com/KRoperUK/mozillion-hass/issues/29)) ([0b2fc3d](https://github.com/KRoperUK/mozillion-hass/commit/0b2fc3d6400a205de3b9ba3b53ea795730a723e4))
* expose the number port status, plan settings and billing detail ([#50](https://github.com/KRoperUK/mozillion-hass/issues/50)) ([9199f8f](https://github.com/KRoperUK/mozillion-hass/commit/9199f8f9e7a15dca845cba305c075e861c8fb2b5))
* repair the integration against Mozillion's sim_meta_id API ([#22](https://github.com/KRoperUK/mozillion-hass/issues/22)) ([4a44a53](https://github.com/KRoperUK/mozillion-hass/commit/4a44a53692a50ddc85d69e3a7b1efd29320eed72))
* ship low-data and overspend blueprints ([#34](https://github.com/KRoperUK/mozillion-hass/issues/34)) ([45c200c](https://github.com/KRoperUK/mozillion-hass/commit/45c200c48361a1ecf3de1ce4afe389cbab2f62d2))


### Bug Fixes

* **deps:** stop pinning homeassistant against the framework's own pin ([#17](https://github.com/KRoperUK/mozillion-hass/issues/17)) ([87eaf01](https://github.com/KRoperUK/mozillion-hass/commit/87eaf0193c377dcad543d25268643594b1e4ccf5))
* follow Home Assistant's types, which were hiding four real errors ([#41](https://github.com/KRoperUK/mozillion-hass/issues/41)) ([4e3212b](https://github.com/KRoperUK/mozillion-hass/commit/4e3212b0aa1f29e3b1c5a80e40646bd65341df90))
* keep identifiers out of logs, diagnostics and repairs ([#51](https://github.com/KRoperUK/mozillion-hass/issues/51)) ([ca44ceb](https://github.com/KRoperUK/mozillion-hass/commit/ca44ceb4afbc70dfb997ad026c7b12163fd3d314))
* let an interrupted migration finish instead of failing forever ([#46](https://github.com/KRoperUK/mozillion-hass/issues/46)) ([2babe2c](https://github.com/KRoperUK/mozillion-hass/commit/2babe2c6f1c4cee06e5d6dbf7a936f0e4ad53a99))
* make the SIM subentry flow work at all (add and reconfigure) ([#42](https://github.com/KRoperUK/mozillion-hass/issues/42)) ([54b9409](https://github.com/KRoperUK/mozillion-hass/commit/54b9409f8634dec6c744d620e99555e4d0b861b1))
* renew an expired stored session when migrating a legacy entry ([#28](https://github.com/KRoperUK/mozillion-hass/issues/28)) ([d187f28](https://github.com/KRoperUK/mozillion-hass/commit/d187f28d6a02f1491e6ad4d0e0a3ff13c1e8fda6))
* roll the reset date to the next month, not the next year ([#35](https://github.com/KRoperUK/mozillion-hass/issues/35)) ([7cfac6b](https://github.com/KRoperUK/mozillion-hass/commit/7cfac6b19e9c89cbf46d559159941d64f37a98ce))
* tell the user about rate limits and dashboard breakage ([#32](https://github.com/KRoperUK/mozillion-hass/issues/32)) ([f8b13b9](https://github.com/KRoperUK/mozillion-hass/commit/f8b13b95d4e119b2fd3fde273fe4fa3766028e8d))
* translate the subentry flow, and claim strict-typing ([#43](https://github.com/KRoperUK/mozillion-hass/issues/43)) ([e83b121](https://github.com/KRoperUK/mozillion-hass/commit/e83b1215a955d7e41a7a7b5dbd4ca452de5f1e01))


### Performance Improvements

* refresh the dashboard page on a slower cadence (plus the roaming-cap caveat) ([#33](https://github.com/KRoperUK/mozillion-hass/issues/33)) ([c6d1026](https://github.com/KRoperUK/mozillion-hass/commit/c6d1026d388c3eed873e0175ddc17e027e18e96a))

## [0.6.1](https://github.com/KRoperUK/mozillion-hass/compare/v0.6.0...v0.6.1) (2026-07-09)


### Bug Fixes

* move reauth_successful to config.abort in translations ([f20760f](https://github.com/KRoperUK/mozillion-hass/commit/f20760f74e62238d4bea2980797e7e9509402afc))

## [0.6.0](https://github.com/KRoperUK/mozillion-hass/compare/v0.5.1...v0.6.0) (2026-07-09)


### Features

* initial ([4e4cac3](https://github.com/KRoperUK/mozillion-hass/commit/4e4cac344e04c3a9ce12b218263f3cb9e5de1528))
* initial mozillion home assistant integration ([b58ace2](https://github.com/KRoperUK/mozillion-hass/commit/b58ace2193b08dd1014e922af659a1c63e6aa9ad))
* overhaul ([e995113](https://github.com/KRoperUK/mozillion-hass/commit/e995113cffa7b8e27ec0997786db6a9420789fa3))
* overhaul ([5fe42b2](https://github.com/KRoperUK/mozillion-hass/commit/5fe42b23b7f953fca9a9c7f73e757dfd0700d4bd))


### Bug Fixes

* **coordinator:** transparently re-authenticate on expired sessions ([3e56168](https://github.com/KRoperUK/mozillion-hass/commit/3e561684a43f99dab6ec92dc07c2a91621326a5c))
* **coordinator:** transparently re-authenticate on expired sessions ([3e56168](https://github.com/KRoperUK/mozillion-hass/commit/3e561684a43f99dab6ec92dc07c2a91621326a5c))
* **coordinator:** transparently re-authenticate on expired sessions ([a3eafa2](https://github.com/KRoperUK/mozillion-hass/commit/a3eafa25900f4f5f93a5cfa9bd8b022e419f3798))
* **docs:** add missing upload-pages-artifact step to docs workflow ([46f815d](https://github.com/KRoperUK/mozillion-hass/commit/46f815d792a5d100911d02e14eb2a6588e187a03))
* **docs:** add missing upload-pages-artifact step to docs workflow ([0076647](https://github.com/KRoperUK/mozillion-hass/commit/0076647b5789cea48b92ad39d1ed0a5f8c9843cc))
* **hacs:** add country GB to hacs.json and resolve dependency clashes ([#7](https://github.com/KRoperUK/mozillion-hass/issues/7)) ([fced025](https://github.com/KRoperUK/mozillion-hass/commit/fced025d27a9a2a7694bdaf1809adfd38b052e8c))

## [0.5.1](https://github.com/KRoperUK/mozillion-hass/compare/dev-v0.5.0...dev-v0.5.1) (2026-07-08)


### Bug Fixes

* **hacs:** add country GB to hacs.json and resolve dependency clashes ([#7](https://github.com/KRoperUK/mozillion-hass/issues/7)) ([fced025](https://github.com/KRoperUK/mozillion-hass/commit/fced025d27a9a2a7694bdaf1809adfd38b052e8c))

## [0.5.0](https://github.com/KRoperUK/mozillion-hass/compare/dev-v0.3.0...dev-v0.5.0) (2026-02-14)


### Features

* overhaul ([e995113](https://github.com/KRoperUK/mozillion-hass/commit/e995113cffa7b8e27ec0997786db6a9420789fa3))

## [0.3.0](https://github.com/KRoperUK/mozillion-hass/compare/4e4cac344e04c3a9ce12b218263f3cb9e5de1528...dev-v0.3.0) (2026-02-14)


### Features

* initial ([4e4cac3](https://github.com/KRoperUK/mozillion-hass/commit/4e4cac344e04c3a9ce12b218263f3cb9e5de1528))
* initial mozillion home assistant integration ([b58ace2](https://github.com/KRoperUK/mozillion-hass/commit/b58ace2193b08dd1014e922af659a1c63e6aa9ad))
* overhaul ([5fe42b2](https://github.com/KRoperUK/mozillion-hass/commit/5fe42b23b7f953fca9a9c7f73e757dfd0700d4bd))
