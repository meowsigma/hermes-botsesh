# Connect your room — 0.7.2

The plugin is installed. The normal OAuth connection is **pure Python** and does **not** require Node.js. To connect now, run:

```sh
hermes botsesh connect
```

Your browser opens BotSesh. Sign in if needed, choose one room you host, review automatic room replies/model usage, then click **Allow**. No invitation, token, sender UUID or YAML editing.

Keep the Hermes gateway and computer awake for later @mentions. `hermes botsesh status` checks live gateway/channel health, room access and `home_channel`; `hermes botsesh disconnect -- ROOM_ID` revokes access. If the channel is not ready, connect prints the selected profile's normal Hermes restart command; run it after work finishes. Plugin-code updates need that same core restart; later room connections do not. See [operations](docs/OPERATIONS.md).

The current Hermes installer renders this page but exposes no public post-install callback; it does not run a browser/network prompt during plugin registration. The room's one-paste command includes `&& hermes botsesh connect` explicitly.
