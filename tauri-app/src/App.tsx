import { useState, useCallback, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Live2DCanvas } from "./components/Live2DCanvas";
import { useControlChannel } from "./hooks/useWebSocket";

function App() {
  const [fps, setFps] = useState(0);
  const [chatText, setChatText] = useState("");
  const [secret, setSecret] = useState("");
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; text: string }[]
  >([]);

  useEffect(() => {
    async function startBackend() {
      try {
        const s = await invoke<string>("get_shared_secret");
        setSecret(s);
      } catch {
        try {
          const backendDir = "G:/projects/deskpet/backend";
          const pythonPath = "G:/projects/deskpet/backend/.venv/Scripts/python.exe";
          const s = await invoke<string>("start_backend", { pythonPath, backendDir });
          setSecret(s);
        } catch (err) {
          console.error("Failed to start backend:", err);
        }
      }
    }
    startBackend();
  }, []);

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
  const lastMsg = messages[messages.length - 1];

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
        modelPath="/assets/live2d/hiyori/hiyori_pro_t10.model3.json"
        width={400}
        height={500}
        onFpsUpdate={handleFpsUpdate}
      />

      {lastMsg && (
        <div
          style={{
            position: "absolute",
            bottom: "80px",
            left: "50%",
            transform: "translateX(-50%)",
            backgroundColor:
              lastMsg.role === "user"
                ? "rgba(59, 130, 246, 0.9)"
                : "rgba(255, 255, 255, 0.9)",
            color: lastMsg.role === "user" ? "white" : "#333",
            borderRadius: "12px",
            padding: "10px 14px",
            maxWidth: "300px",
            fontSize: "13px",
            boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
            zIndex: 10,
          }}
        >
          {lastMsg.text}
        </div>
      )}

      <div
        style={{
          position: "absolute",
          bottom: "10px",
          left: "10px",
          right: "10px",
          display: "flex",
          gap: "8px",
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
            padding: "8px 12px",
            borderRadius: "20px",
            border: "1px solid #ddd",
            fontSize: "13px",
            backgroundColor: "rgba(255,255,255,0.95)",
            outline: "none",
          }}
        />
        <button
          onClick={handleSend}
          disabled={state !== "connected" || !chatText.trim()}
          style={{
            padding: "8px 16px",
            borderRadius: "20px",
            border: "none",
            backgroundColor:
              state === "connected" ? "#3b82f6" : "#ccc",
            color: "white",
            fontSize: "13px",
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
