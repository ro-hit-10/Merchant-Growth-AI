import { useEffect, useRef, useState } from "react";

export default function ChatPanel({ messages, onSend, busy }) {
  const [text, setText] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, busy]);

  const submit = () => {
    if (!text.trim() || busy) return;
    onSend(text.trim());
    setText("");
  };

  return (
    <>
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-intro">
            <div className="chat-intro-title">Hi, I'm Vriddhi 👋</div>
            <div className="chat-intro-sub">
              Ask me about your sales, stock, an upcoming festival, a loan or insurance offer, or a problem with your
              Soundbox, QR code, or the app.
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div className={`chat-bubble-row ${m.role}`} key={i}>
            <div className={`chat-bubble ${m.role}`}>{m.text}</div>
          </div>
        ))}
        {busy && (
          <div className="chat-bubble-row vriddhi">
            <div className="chat-bubble vriddhi typing">
              <span className="dot" /><span className="dot" /><span className="dot" />
            </div>
          </div>
        )}
      </div>
      <div className="trace-chat">
        <input
          placeholder="Message Vriddhi…"
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <button onClick={submit} disabled={busy}>Send</button>
      </div>
    </>
  );
}
