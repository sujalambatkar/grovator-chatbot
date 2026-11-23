/** Incremental Server-Sent-Events parser. Handles chunk boundaries inside lines/events and CRLF. */
export interface RawEvent {
  event: string;
  data: string;
}

export function createSseParser() {
  let buffer = "";
  return {
    push(chunk: string): RawEvent[] {
      buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      const out: RawEvent[] = [];
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (!line || line.startsWith(":")) continue; // comment / keep-alive
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "event") event = value;
          else if (field === "data") data.push(value);
        }
        if (data.length) out.push({ event, data: data.join("\n") });
      }
      return out;
    },
  };
}
