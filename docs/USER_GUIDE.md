# 📍 SpotBot User Guide

*How to turn the reels your friends share into actual plans — no commands to memorize.*

SpotBot lives in your Discord server. You paste an Instagram link; it figures out
the place, posts a card everyone can vote on, and when enough people are in, it
puts the outing on the server calendar. That's the whole idea. Here's everything
in detail.

---

## 1. Save a spot (the only thing you *have* to know)

**Paste an Instagram reel or post link — or a TikTok — into any channel.** That's
it. No command, no setup.

What you'll see:
1. ⏳ appears on your message — SpotBot picked it up
2. A status reply: *"🔎 Reading that reel — caption, audio, and location. Takes ~30s…"*
3. The status message **turns into a spot card**: the place's name, a thumbnail,
   what kind of spot it is, when it's happening (if it's an event), a map link —
   and voting buttons

SpotBot reads more than the caption: it **listens to the audio** (lots of reels
say the venue out loud without writing it) and can read **text typed over the
video**. If a time like "this Friday 8pm" is mentioned, the card shows the real
date.

**Where pasting works:** every channel SpotBot can read, plus DMs. DM captures go
to your own private stash instead of the server's list.

> 🔇 Don't want captures in a channel (e.g. #serious-talk)? A mod runs `/mute`
> there. Run it again to turn capture back on.

### If a capture fails
Some reels are private, deleted, or Instagram is being Instagram. You'll get an
honest message plus two buttons:
- **🔁 Retry** — often works on the second try
- **✍️ Add manually** — a tiny form (place name + the vibe) so the spot gets saved
  anyway. Your paste is never wasted.

---

## 2. Vote (this is where the magic starts)

Every spot card has buttons:

| Button | What it does |
|---|---|
| 👍 **Want to go** | Puts your name on the card — it literally shows *"Who's in: nick, sam, jo"* |
| 👎 **Not for me** | Takes your name back off |
| ✨ **Add suggestions** | SpotBot posts up to 3 *similar* saved spots as new cards |
| ✏️ **Edit** | Fix a wrong venue name or vibe — a form opens pre-filled, anyone can correct it |
| 🗑 **Remove** | Deletes the spot (mods only) |

You can't double-vote — tapping 👍 twice still counts once. Changing your mind is
always allowed.

## 3. Lock it in → it's on the calendar

When enough people tap 👍 (**3 by default**), SpotBot announces:

> 🎉 **3 people want Casa Loma** — lock it in as a server event?

Tap **📅 Lock it in** and SpotBot creates a real **Discord Scheduled Event**:
- If the reel mentioned a date/time, it uses that
- Otherwise it defaults to **next Friday, 7pm**
- The event's location is a map link, so nobody asks "wait, where is it?"

Everyone in the server can hit "Interested" on the event and get Discord's own
reminders. Chat → calendar, no organizer required.

---

## 4. Plan an outing from the chat — `/plan`

You've been chatting: *"I'm starving" · "something cheap" · "tacos??"*. Type:

```
/plan
```

SpotBot reads the recent conversation, posts a **"Here's what I heard"** card
(vibe, area, budget, when), opens a thread, and drops its best picks from your
group's saved spots — each as a votable card. Keep chatting and `/plan` again to
refine.

Three things make the picks smarter:
- **Say a place by name** ("Casa Loma was so nice") → it's 📌 pinned to the top.
- **Say where you are** ("I'm in Long Beach" / two people in different places)
  → picks rank by distance, or by the *midpoint* between you.
- **Places you actually went** (confirmed after an event) outrank never-tried ones.

**Know exactly what you want?** Search directly:

```
/events late night tacos
/events somewhere chill for a date
```

## 5. The rest of the toolbox

| Command | What it does |
|---|---|
| `/setup` | 20-second explainer + this channel's settings (only you see it) |
| `/browse` | Flip through saved spots by category (🍽️ 🍸 🎶 …) as votable cards |
| `/catalog` | The leaderboard — your server's top-voted spots |
| `/digest` | One-message summary: spot count, top picks, nights out |
| `/share` | The link to your server's public catalog web page |
| `/mute` | Toggle capture off/on in the current channel *(mods)* |
| `/suggestions on` | Let SpotBot chime in (in a tidy thread) when the chat sounds like "we should go out" *(mods)* |
| `/help` | The quick how-to card |

**The share page:** your server's whole catalog also lives on a web page — spots,
votes, who's in, map links — that you can send to friends who aren't on Discord.
Ask whoever runs the bot for the link.

---

## 6. Good to know

- **First time in a server?** After the first-ever card, SpotBot posts a one-time
  tip explaining votes → calendar. That's the only time it explains itself
  unprompted.
- **Privacy:** the AI that reads reels runs on the host's own machine — captions
  and audio never go to some company's cloud. Only the URLs you paste are fetched.
- **It's your group's taste, not ads.** SpotBot only ever suggests spots *someone
  in your group saved*. The more reels you paste, the smarter `/plan` gets.
- **Speed:** a capture takes ~20–45 seconds depending on the host machine — it's
  rendering the page, listening to the audio, and running a local AI. The status
  message keeps you posted.

## 7. When something looks wrong

| You see | What it means |
|---|---|
| "I couldn't read that reel" | It's private/removed, or IG walled it. Retry once, then Add manually. |
| "I read it, but couldn't find a venue" | The reel had no place info anywhere. Add manually if you know it. |
| "Can't reach the catalog service" | The host's admin app isn't running — ping your server's bot owner. |
| Card has "no fixed date" | The reel never mentioned a time. It's saved as an idea, not an event. |
| Suggestions feel empty | The catalog is young — paste ~10 reels and try again. |

*Happy hunting. Every reel you paste is a night out waiting to happen.* 🌃
