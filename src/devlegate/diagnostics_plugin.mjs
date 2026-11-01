// Copyright (c) 2026 Daniil Romanov
// SPDX-License-Identifier: EUPL-1.2
/*
 * OpenCode attaches workspace-wide LSP diagnostic snapshots to edit-tool
 * metadata. Unrelated and unchanged diagnostics can be repeatedly serialized
 * into session history and JSONL events; their size is not bounded by the edit.
 * Bound this redundant metadata without modifying tool output or disabling
 * automatic LSP feedback for coding agents.
 */
import path from "node:path"

const limit = 65536
const size = (value) => Buffer.byteLength(JSON.stringify(value), "utf8")

export default async function diagnosticsPlugin({ directory }) {
  return {
    "tool.execute.after": async (input, result) => {
      if (!["apply_patch", "edit", "write"].includes(input.tool)) return
      const metadata = result?.metadata
      if (!metadata || typeof metadata !== "object" || !("diagnostics" in metadata)) return
      const diagnostics = metadata.diagnostics
      const touched = new Set()
      const add = (name) => {
        if (typeof name === "string" && name && !name.includes("\0"))
          touched.add(path.resolve(directory, name))
      }
      if (input.tool === "apply_patch" && Array.isArray(metadata.files)) {
        for (const file of metadata.files) {
          if (!file || typeof file.filePath !== "string" || !path.isAbsolute(file.filePath)) {
            touched.clear()
            break
          }
          add(file.filePath)
          if (file.type === "move") {
            if (typeof file.movePath !== "string" || !path.isAbsolute(file.movePath)) {
              touched.clear()
              break
            }
            add(file.movePath)
          }
        }
      } else if (input.tool !== "apply_patch") add(input.args?.filePath)

      const filtered = Object.create(null)
      let truncated = false
      let bytes = 2 // braces
      if (diagnostics && typeof diagnostics === "object" && !Array.isArray(diagnostics)) {
        const candidates = []
        for (const key of Object.keys(diagnostics).sort()) {
          if (!path.isAbsolute(key) || !touched.has(path.normalize(key))) continue
          if (!Array.isArray(diagnostics[key])) { truncated = true; continue }
          for (const diagnostic of diagnostics[key]) candidates.push([key, diagnostic])
        }
        candidates.sort((a, b) => {
          const rank = (d) => d?.severity === 1 ? 0 : d?.severity === 2 ? 1 : 2
          return rank(a[1]) - rank(b[1])
        })
        for (const [key, diagnostic] of candidates) {
          const exists = Object.hasOwn(filtered, key)
          const extra = size(diagnostic) + (exists ? 1 : size(key) + 3 + (bytes > 2 ? 1 : 0))
          if (bytes + extra > limit) {
            truncated = true
            continue
          }
          if (!exists) filtered[key] = []
          filtered[key].push(diagnostic)
          bytes += extra
        }
      } else truncated = true
      if (!touched.size && diagnostics && Object.keys(diagnostics).length) truncated = true
      metadata.diagnostics = filtered
      if (truncated) metadata.devlegateDiagnosticsTruncated = true
    },
  }
}
