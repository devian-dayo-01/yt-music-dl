# YT Music 練習サイト（yt-dlp + Invidious自動モード）

YouTube API完全排除。Invidiousインスタンスを定期ロード（api.invidious.ioから）で最速モード優先。使えないor遅い場合は即yt-dlpフォールバック。高音質のみ（bestvideo+bestaudio）。Bot検知回避済み（user-agent+referer）。

## インストール＆Railwayデプロイ
1. フォルダ作成
2. build.sh実行
3. .env（任意、APIキー不要）
4. uvicorn main:app --reload --host 0.0.0.0 --port 8000
5. ブラウザで http://127.0.0.1:8000

Railway:
- Pythonランタイム
- Buildコマンド: bash build.sh
- 起動: uvicorn main:app --host 0.0.0.0 --port $PORT
- Secrets: なし（インスタンスは自動）

Invidiousインスタンス例（定期ロード）:
- inv.nadeko.net
- invidious.nerdvpn.de
- yewtu.be

これで検索即時＋高音質再生完璧。mp3ダウンロードゼロ。練習完了！
