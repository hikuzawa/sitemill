# ADR 0001: Python 3.12 + uv と主要ライブラリの選定

- ステータス: 採用（2026-09-10）

## 背景
エンジンはローカル PC・Raspberry Pi・GitHub Actions のどれでも同じように動く必要があり、AI が保守する前提で依存は少なく、宣言的に書けるものがよい。

## 決定
- Python は 3.12 以上。`.python-version` で 3.12 を固定し、パッケージ管理と仮想環境は uv に任せる
- src レイアウト（`src/sitemill`）、ビルドは hatchling、lint/format は ruff、テストは pytest
- HTTP は httpx（同期）、robots.txt は protego、HTML 解析は selectolax、モデルは pydantic v2、設定は pydantic-settings、テンプレートは Jinja2、CLI は typer、設定ファイルは YAML（pyyaml）と TOML（tomllib）
- LLM は Anthropic SDK（ADR 0009）
- テストでの HTTP モックは respx

## 理由
- uv は aarch64 Linux（Raspberry Pi）にも同じ Python を配布でき、CI でも 1 コマンドで環境が揃う
- 巡回は礼儀正しく間隔を空けるため並列性は不要で、同期 httpx が最も単純
- selectolax は自治体 CMS の崩れた HTML に寛容で高速
- pydantic のモデルから JSON Schema を生成して LLM の構造化出力にそのまま使える

## 影響
- 3.14 系が手元にあっても使わない（ネイティブ拡張のホイール互換のため）
- 依存を足すときは「Pi でホイールがあるか」「AI が保守できるか」を基準に ADR を追記する
