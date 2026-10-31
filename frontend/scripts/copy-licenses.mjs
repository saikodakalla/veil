import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = new URL("../", import.meta.url);
const output = new URL("dist/licenses/", root);
await mkdir(output, { recursive: true });
for (const name of ["react", "react-dom", "scheduler", "lucide-react"]) {
  const text = await readFile(
    new URL(`node_modules/${name}/LICENSE`, root),
    "utf8",
  );
  await writeFile(new URL(`${name}.txt`, output), text);
}
console.log(`Bundled third-party license texts in ${fileURLToPath(output)}`);
