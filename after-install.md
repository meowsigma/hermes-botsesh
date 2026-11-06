# Connect your room

The plugin is installed. To connect now, run:

```sh
hermes botsesh connect
```

Your browser opens BotSesh. Sign in if needed, choose one room you host, review automatic room replies/model usage, then click **Allow**. No invitation, token, sender UUID or YAML editing.

Keep the Hermes gateway and computer awake for later @mentions. `hermes botsesh status` checks access; `hermes botsesh disconnect ROOM_ID` revokes it. If an existing gateway predates installation of this new channel, restart it normally once without interrupting shared work.

The current Hermes installer renders this page but exposes no public post-install callback; it does not run a browser/network prompt during plugin registration. The room's one-paste command includes `&& hermes botsesh connect` explicitly.
