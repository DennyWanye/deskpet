function App() {
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "flex-end",
        padding: "20px",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(255, 255, 255, 0.9)",
          borderRadius: "12px",
          padding: "12px 16px",
          maxWidth: "300px",
          fontSize: "14px",
          color: "#333",
          boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
        }}
      >
        Hello, I'm your desktop pet!
      </div>
    </div>
  );
}

export default App;
