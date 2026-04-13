import { useState } from 'react'
import './App.css'

function App() {
  const [greetMsg, setGreetMsg] = useState('')
  const [name, setName] = useState('')

  async function greet() {
    // Will invoke the Tauri command when running in Tauri
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      setGreetMsg(await invoke('greet', { name }))
    } catch {
      setGreetMsg(`Hello, ${name}! (running in browser)`)
    }
  }

  return (
    <main className="container">
      <h1>DeskPet</h1>
      <p>Desktop pet application powered by Tauri 2 + React</p>

      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          greet()
        }}
      >
        <input
          id="greet-input"
          onChange={(e) => setName(e.currentTarget.value)}
          placeholder="Enter a name..."
        />
        <button type="submit">Greet</button>
      </form>

      <p>{greetMsg}</p>
    </main>
  )
}

export default App
