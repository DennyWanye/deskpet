import { useState, useCallback } from "react";
import { Live2DCanvas } from "./components/Live2DCanvas";

function App() {
  const [fps, setFps] = useState(0);
  const handleFpsUpdate = useCallback((newFps: number) => setFps(newFps), []);

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

      <div
        style={{
          position: "absolute",
          bottom: "20px",
          left: "50%",
          transform: "translateX(-50%)",
          backgroundColor: "rgba(255, 255, 255, 0.9)",
          borderRadius: "12px",
          padding: "12px 16px",
          maxWidth: "300px",
          fontSize: "14px",
          color: "#333",
          boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
          zIndex: 10,
        }}
      >
        Hello, I'm your desktop pet!
      </div>

      <div
        style={{
          position: "absolute",
          top: "4px",
          right: "4px",
          fontSize: "10px",
          color: fps >= 30 ? "lime" : "red",
          backgroundColor: "rgba(0,0,0,0.5)",
          padding: "2px 6px",
          borderRadius: "4px",
          zIndex: 20,
        }}
      >
        {fps} FPS
      </div>
    </div>
  );
}

export default App;
