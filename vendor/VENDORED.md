# Vendored code

Copied byte for byte by `tools/vendor.py`. Nothing here is edited; a change belongs upstream.

- agent-si: https://github.com/joelbrilliant/agent-si at fe4657e1378dbbd4334fa9d99885b1263241849e (`src/brain/`, the modules the plugin imports, plus LICENSE and NOTICE,
  and `examples/demo/` as `demo/`).
- agent-memory: https://github.com/joelbrilliant/agent-memory at 26ebe0f91dbcd58ec74676069fb9f8470c60a622 (`session_continuity.py`, `session_adapters.py`,
  `db_permissions.py`, `ingest.py`, plus LICENSE and NOTICE).

Runtime adjustment, in this plugin's `history.py` and not in these files: `session_adapters.discover_hermes`
is replaced by a version that also finds sessions current Hermes writes with `source = 'local'` and the
surface in `created_source`. The same change is proposed upstream.

| file | sha256 |
|---|---|
| vendor/brain/__init__.py | a22b5a9a2b182e9de5ac5237b86bfee5908921e60e4fd8192dcb3352654d870f |
| vendor/brain/advice.py | 6d83c40adcc886aa051628cb4cb5be843b5b7bd61d620b6fe13d6fb36b9b006e |
| vendor/brain/applier.py | 58e07b582f3b7f341f59ddb773fa4aaaff79c827e46a73bccabb6bb12bb8451b |
| vendor/brain/candidates.py | f1bfc0cc60007b81d116f5c24c88eac82e57d5cbd39aca5ae273a17fde626c22 |
| vendor/brain/collect.py | c675139e09c2be352fa4735bc423c19d7c2dd4c8632854f6eeff022e04bbc16e |
| vendor/brain/config.py | 630fcace4d036219443d56180b3e2d73552b1eb907b796ff9bd7face7f1b36f0 |
| vendor/brain/evidence_gate.py | 56dca7ff835c06667a040f59dd22a087921efbd2d76d499a6e45584b3ba9e175 |
| vendor/brain/ingress.py | 857158f1b8ece7ceb23016b171f34df678f60e281c18a9c3b663bb53d60579aa |
| vendor/brain/init.py | b493b6c375716e9348a9104bbb21c9a45320c443a82e6b1fa9a4a1f394d9f210 |
| vendor/brain/marks.py | cad12487ad6af7c777fc73aec5af36ba106f6b0aa61ce4aa8ee10209976e16e1 |
| vendor/brain/migrate.py | 68632c786d3a17c14b2862eff59e6f37c1ce3cbdaeddf63d7c2d75528c10dc28 |
| vendor/brain/reads.py | f445dcda51ecb9f5dabe397af487210fe3ac570d8799c524a5f71a8c347b3ead |
| vendor/brain/redact_ops.py | 2bc18776031ddd5712622ef62e0d3b50430e2eae5fe8ec62a16a422ec237092a |
| vendor/brain/registry.py | 5a4711c685565a9e5d0bc9dc9e9f0e46482d2b7ba4352adeb03fec8d5f11ef10 |
| vendor/brain/schemas.py | 561566991db0476ea4c1873795fa6a6c811646b025705afaf7aae4b3a31332bf |
| vendor/brain/scope.py | f6f6740d3e1a3bd1c324317bed07b979f46e7ed5962efae20ff2ff496b734a08 |
| vendor/brain/scrub.py | fbebc7f84e1838f7e02f3ae5a903773bb285a85c4305969f2e901d45f6045fe5 |
| vendor/brain/store.py | 627de899f7de55a8c812b483434e31d23702ca3c189ddf2048c269aa386a356b |
| vendor/brain/strict.py | 108d318a009f5ac79834c144362bacd38c833111cc34f5e096720439971ea60c |
| vendor/brain/text.py | c35ee04adddc9d27481314adf09ba18d7bce1afd77bbdb59c521934e479063fc |
| vendor/brain/truth.py | 7149516c40a6409429e99cae83a350d80ed15ac00450d736a87c16ac67092aa8 |
| vendor/brain/units.py | 46c9117c2d7fb2142c9db28a627fa1077fb568f8cd36d2b8fea74fdbf799cf0d |
| vendor/brain/validate.py | fb20890189ccb9e7ebd95b0dd2bf4c6c2920fe79f7d26cb0edf8400512935e6a |
| vendor/brain/versions.py | 6358b41b913755e7b3eb9d00a453e2d33e730ca0c4e58181d729e877cdebacf6 |
| vendor/brain/collectors/__init__.py | 22b512a03ddabae17219488d99ce1033568199e47914744d1457a11da3733e3f |
| vendor/brain/collectors/base.py | 3dc605a2ee0078fb3cbb0521c11551ca008a27b69538babc290eeaedbf5fdfc8 |
| vendor/brain/collectors/calendar.py | ae6fe1fa1bc9d5a71ee0448beaade92fe16aa10a371ce563178b0c91a3ddc5eb |
| vendor/brain/collectors/capture.py | 3c64cdea8f8a6a7ab0963667551c470ca54de98eba0b3da2fd4f4c1c9c074b04 |
| vendor/brain/collectors/files.py | 2e9b5538aaf0deee3351f1dc19b5fe1e121f5d7fefacb354e4981d9ea2d96ab9 |
| vendor/brain/collectors/github.py | f46b40cefb4d58a3fc34f18b8576bfa3090d1a93a5f26f1376d1fc4cfcb2ed9e |
| vendor/brain/collectors/messages.py | 713016cf718f5b510f452663cb21f371c75b78faf05f55479eb1df5d6baad7c4 |
| vendor/brain/collectors/privacy.py | 5535943e6c264ba4cad16a142ac63ed8f381f5fd34dd792b5839da2c26d4e20f |
| vendor/brain/collectors/vanta.py | c92fa17082624b8182032068617b9763e8f121e9e6073f0b4b0047d489ede25e |
| vendor/brain/migrations/0001_core.sql | 32035cad8f376534dd0534c65a1bb263fc2f12e8a62545c771f4907bca6fbc70 |
| vendor/brain/migrations/0002_search.sql | 3fd4fa229216acedc6617613cf95fea866528ac34abfb32e76a0af1ba9dca62e |
| vendor/brain/migrations/0003_meetings.sql | 44cd4204be83bdfe50d50a62e9bc688740e3ca4d50793e7b90077c3c8aeaa087 |
| vendor/brain/migrations/0004_people_and_unchecked.sql | a7a7c32a3b0e8e0ffd27cadc9a45829e5162d978378c6369978e067e25b4c2da |
| vendor/brain/migrations/0005_truth.sql | 2642a4263f5f3b11c87bb2ce46228e1397c62288676968d9000538319b481d90 |
| vendor/brain/migrations/0006_redaction.sql | 5ca05787e185e2f996bdee43155c887cbc0603b1ea1d2c0edc43aa34040f15ea |
| vendor/brain/migrations/0007_folders.sql | 9ef6fbc85b03e4c1986654281b504161b5d125f3990175ed96731f4680d556af |
| vendor/brain/migrations/0008_links.sql | 69398fc103e5282b3d3cb5ede2a450f19186d0baf994717ed6f2c93af102560e |
| vendor/brain/migrations/0009_source_author.sql | f0bcb4ca70b1159aabc5d3daf81739d015ad4f48425b0f1c2d17b93c59eac1e4 |
| vendor/brain/templates/ROUTER.md.template | 46871ec8529cd84a2b5d8d767f9ac6415371828776959c1a805e3b4ef51a0154 |
| vendor/brain/templates/brain.toml.template | 58bdbc9d5f142241996dd9d677fb9e49f3c3536478b6a618e0e7c860729e8682 |
| vendor/LICENSE-agent-si | 517dea67b88518fbe5bcaeff69711720730aa6e32b5879e3ea5e62c644f1947b |
| vendor/LICENSE-agent-memory | 517dea67b88518fbe5bcaeff69711720730aa6e32b5879e3ea5e62c644f1947b |
| vendor/NOTICE-agent-si | 57376aaa63e362dfcaa17f49c2cc17ecb542d466e712168470eb3041c18518a8 |
| vendor/NOTICE-agent-memory | 70a048e5448c73f48ba21bfc2ff5ca7427c7eb9d7fd598cf37cf5f2769be256a |
| vendor/session_continuity.py | 527b8f3da6834b9a7d6548260ea433f8cbcd26353b8aa9c94f9d80c38a3343fb |
| vendor/session_adapters.py | ae55ebf64420d4b65f6cf662f9ea20a0782bbfa45a16622c846c0e38d2b4cbb0 |
| vendor/db_permissions.py | 64f005f7beb10c5a09e930ec39a50705646dffc1364cce8d8edd106d939c40cb |
| vendor/ingest.py | 5cbc5fc7be73f94f2a9378f8b6112bbdc2163f8255840a02cd82a221a77c7a9d |
| demo/demo.toml | 3bc0a4e785dea8cdf79513bc658e949ea3a4fddde7034d3db2dfcc56abe07d33 |
| demo/fixtures/mail.json | 4d37cadc5ebca77982daf9e6c6f25906082fb1fe8d9706e72149938a9a8a8df7 |
| demo/packets/1-notes.json | 20e2158834d55d58a9f280c68a8f7627899de6aeedb2395bd20af14005363ddc |
| demo/packets/2-work.json | f09e3733614b8c5bdc6618db542b7fbfd2170c05cbaba87f4c83d58d0a39da7c |
| demo/questions.json | caea2292577869170569e5cb8919dfd9e78f563841240f4c53280c4240205e9f |
