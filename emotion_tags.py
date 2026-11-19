"""Mood markers for the avatar. The conversation prompt asks the LLM to start each reply with one marker,
e.g. 【うれしい】. MoodFilter takes it out of the streamed reply before anything is spoken, captioned or
saved, and reports which emotion it stood for.

    uv run python emotion_tags.py      # self-check
"""

#marker text -> VRM expression preset (neutral = no expression)
MOODS = {"普通": "neutral", "うれしい": "happy", "びっくり": "surprised", "かなしい": "sad", "おだやか": "relaxed"}
OPEN, CLOSE = "【", "】"
MAX_LEN = 10  #longest marker text we wait for; a longer 【…】 is ordinary text


class MoodFilter:
    """Feed it the reply chunk by chunk; it returns the text that may go on, without mood markers.

    - A 【…】 at the very start is always removed, known or not, so an invented marker is never read aloud.
    - Later on only known markers are removed; any other 【…】 is the speaker's own text and passes through.
    - `emotion` is None until the start of the reply has been seen, then the first marker's emotion
      ("neutral" if the reply started without one).
    """

    def __init__(self):
        self.emotion = None
        self._held = ""        #text from a 【 that may still turn out to be a marker
        self._started = False  #some real text has gone out

    def feed(self, text: str) -> str:
        self._held += text
        out = []
        while self._held:
            if not self._started:
                self._held = self._held.lstrip()  #no blank before or after a leading marker
                if not self._held:
                    break
            start = self._held.find(OPEN)
            if start != 0:  #plain text up to the next 【 (or all of it)
                cut = len(self._held) if start == -1 else start
                out.append(self._held[:cut])
                self._held = self._held[cut:]
                self._begin()
                continue
            end = self._held.find(CLOSE)
            if end == -1 and len(self._held) <= MAX_LEN + 1:
                break  #might still be a marker: wait for more
            name = self._held[1:end].strip() if end != -1 else None
            if name is not None and len(name) <= MAX_LEN and (name in MOODS or not self._started):
                if self.emotion is None:
                    self.emotion = MOODS.get(name, "neutral")
                self._held = self._held[end + 1:]
            else:  #not a marker: the bracket is text
                out.append(OPEN)
                self._held = self._held[1:]
                self._begin()
        return "".join(out)

    def flush(self) -> str:
        """End of the reply: whatever was still being held is text after all."""
        rest, self._held = self._held, ""
        if rest:
            self._begin()
        return rest

    def _begin(self):
        self._started = True
        if self.emotion is None:
            self.emotion = "neutral"


if __name__ == "__main__":
    def run(chunks):
        f = MoodFilter()
        text = "".join(f.feed(c) for c in chunks) + f.flush()
        return text, f.emotion

    cases = [
        (["【うれしい】わあ、楽しそうですね！"], ("わあ、楽しそうですね！", "happy")),
        (["【", "うれ", "しい", "】", "わあ、", "楽しそう"], ("わあ、楽しそう", "happy")),       #marker split over chunks
        (list("【びっくり】へえ、そうですか？"), ("へえ、そうですか？", "surprised")),           #one character at a time
        (["こんにちは！今日も話しましょう。"], ("こんにちは！今日も話しましょう。", "neutral")),  #no marker
        (["【わくわく】いいですね。"], ("いいですね。", "neutral")),                             #unknown marker at the start: dropped
        (["\n【かなしい】 それは大変ですね。"], ("それは大変ですね。", "sad")),                  #whitespace around it
        (["【普通】そうですね。【うれしい】よかったです。"], ("そうですね。よかったです。", "neutral")),  #a second marker: removed, first one counts
        (["これは【重要】です。"], ("これは【重要】です。", "neutral")),                         #a bracket that isn't a marker stays
        (["【おだやか】ゆっくり", "で大丈夫です【"], ("ゆっくりで大丈夫です【", "relaxed")),     #reply ends on an open bracket
        (["【これはとても長い説明の文章です】はい。"], ("【これはとても長い説明の文章です】はい。", "neutral")),  #too long to be a marker
        (["【うれしい"], ("【うれしい", "neutral")),                                             #never closed
        ([""], ("", None)),
    ]
    failed = 0
    for chunks, want in cases:
        got = run(chunks)
        if got != want:
            failed += 1
            print(f"FAIL {chunks!r}\n  want {want!r}\n  got  {got!r}")
    print(f"{len(cases) - failed}/{len(cases)} cases pass")
    raise SystemExit(1 if failed else 0)
