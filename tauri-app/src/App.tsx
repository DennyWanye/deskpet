import { useState, useCallback, useEffect, useRef } from "react";
// import { invoke } from "@tauri-apps/api/core";
import { Live2DCanvas } from "./components/Live2DCanvas";
import { useControlChannel } from "./hooks/useWebSocket";

function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.*?)\*\*/g, '$1')   // **bold**
    .replace(/\*(.*?)\*/g, '$1')        // *italic*
    .replace(/#{1,6}\s/g, '')           // ## headers
    .replace(/`([^`]+)`/g, '$1')        // `code`
    .replace(/---+/g, '')               // ---
    .replace(/\n{3,}/g, '\n\n')         // excessive newlines
    .trim();
}

function App() {
  const [fps, setFps] = useState(0);
  const [chatText, setChatText] = useState("");
  const [secret, _setSecret] = useState("");
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; text: string }[]
  >([]);

  // DEV: backend started manually, connect directly
  // PROD: uncomment to auto-start backend via process manager
  // useEffect(() => {
  //   async function startBackend() {
  //     try {
  //       const s = await invoke<string>("get_shared_secret");
  //       setSecret(s);
  //     } catch {
  //       try {
  //         const backendDir = "G:/projects/deskpet/backend";
  //         const pythonPath = "G:/projects/deskpet/backend/.venv/Scripts/python.exe";
  //         const s = await invoke<string>("start_backend", { pythonPath, backendDir });
  //         setSecret(s);
  //       } catch (err) {
  //         console.error("Failed to start backend:", err);
  //       }
  //     }
  //   }
  //   startBackend();
  // }, []);

  const { state, lastMessage, sendChat } = useControlChannel(8100, secret);

  useEffect(() => {
    if (lastMessage?.type === "chat_response") {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: lastMessage.payload.text },
      ]);
    }
  }, [lastMessage]);

  const handleSend = () => {
    if (!chatText.trim()) return;
    setMessages((prev) => [...prev, { role: "user", text: chatText }]);
    sendChat(chatText);
    setChatText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFpsUpdate = useCallback(
    (newFps: number) => setFps(newFps),
    [],
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <Live2DCanvas
        modelPath="/assets/live2d/hiyori/Hiyori.model3.json"
        onFpsUpdate={handleFpsUpdate}
      />

      {messages.length > 0 && (
        <div
          style={{
            position: "absolute",
            bottom: "55px",
            left: "5px",
            right: "5px",
            maxHeight: "200px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "6px",
            zIndex: 10,
            padding: "4px",
          }}
        >
          {messages.slice(-5).map((msg, i) => (
            <div
              key={i}
              style={{
                alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
                backgroundColor:
                  msg.role === "user"
                    ? "rgba(59, 130, 246, 0.9)"
                    : "rgba(30, 30, 50, 0.85)",
                color: "white",
                borderRadius: "10px",
                padding: "6px 10px",
                maxWidth: "260px",
                fontSize: "12px",
                lineHeight: "1.4",
                maxHeight: "80px",
                overflowY: "auto",
                wordBreak: "break-word",
              }}
            >
              {stripMarkdown(msg.text)}
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      )}

      <div
        style={{
          position: "absolute",
          bottom: "6px",
          left: "6px",
          right: "6px",
          display: "flex",
          gap: "4px",
          zIndex: 20,
        }}
      >
        <input
          type="text"
          value={chatText}
          onChange={(e) => setChatText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            state === "connected" ? "Say something..." : "Connecting..."
          }
          disabled={state !== "connected"}
          style={{
            flex: 1,
            padding: "5px 10px",
            borderRadius: "16px",
            border: "1px solid #ddd",
            fontSize: "12px",
            backgroundColor: "rgba(255,255,255,0.95)",
            outline: "none",
          }}
        />
        <button
          onClick={handleSend}
          disabled={state !== "connected" || !chatText.trim()}
          style={{
            padding: "5px 12px",
            borderRadius: "16px",
            border: "none",
            backgroundColor:
              state === "connected" ? "#3b82f6" : "#ccc",
            color: "white",
            fontSize: "12px",
            cursor: state === "connected" ? "pointer" : "default",
          }}
        >
          Send
        </button>
      </div>

      <div
        style={{
          position: "absolute",
          top: "4px",
          right: "4px",
          display: "flex",
          gap: "6px",
          zIndex: 20,
        }}
      >
        <span
          style={{
            fontSize: "10px",
            color: fps >= 30 ? "lime" : "red",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {fps} FPS
        </span>
        <span
          style={{
            fontSize: "10px",
            color: state === "connected" ? "lime" : "orange",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {state}
        </span>
      </div>
    </div>
  );
}

export default App;
