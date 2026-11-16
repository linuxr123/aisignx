# Emergency messages: saved messages and the API

## Saved messages (templates)

On **Emergency**, fill in the send form and put a name in "Save as a template", then **Save as template**.
Nothing is sent. Saved messages appear in a table above the form with **Send now** (asks to confirm) and
**Remove**. Each has an **API id**, used below.

A template keeps its screens as chosen. If that group or display is later deleted, the template refuses to
send instead of widening to all screens.

## Raising one from a button, script or webhook

1. Create an API token (user menu > My API tokens) with the **emergency:send** permission. Only editors and
   admins can hold it. Give each button or system its own token so you can revoke one without the others.
2. Call the API with `Authorization: Bearer <token>`:

```
GET  /api/v1/emergency/templates               list saved messages
POST /api/v1/emergency/templates/<id>/send     raise it (201; 200 with "already_live": true if it is already up)
GET  /api/v1/emergency                         what is live now
POST /api/v1/emergency/clear                   clear everything; or {"id": 12} for one message
```

```
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:5000/api/v1/emergency/templates/3/send
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:5000/api/v1/emergency/clear
```

Safety by design:

- The API can only send **prepared** messages. A leaked token can repeat "Fire drill" but cannot write a new
  message or choose new screens.
- Sending the same template twice (button pressed twice, webhook retried) does not stack duplicates.
- Every send and clear is audited with the token's owner and `via: api`.
- Use HTTPS if the token travels beyond your LAN ([https.md](https.md)).

Scheduled emergencies (start and end at set times) are not built yet.
