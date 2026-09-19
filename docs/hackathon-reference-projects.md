Yep — here are the best ones to actually study, with **multiple links per project when available**.

- **Detour — satellite collision avoidance / edge agents**
  - GitHub: [github.com/keanucz/detour](https://github.com/keanucz/detour?utm_source=chatgpt.com)
  - Devpost: [Detour on Devpost](https://devpost.com/software/detour-64kpds?utm_source=chatgpt.com)
  - Live site: [Detour demo](https://detour-azure.vercel.app?utm_source=chatgpt.com)  
  This one is especially worth reading because the repo is public and the architecture is clear: LangGraph agents + Nemotron + local/edge inference for orbital-debris response. 

- **Nomi — multi-sensor senior safety system**
  - GitHub: [github.com/Crustaly/nomi](https://github.com/Crustaly/nomi?utm_source=chatgpt.com)
  - Devpost: [Nomi on Devpost](https://devpost.com/software/nomi-aoim58?utm_source=chatgpt.com)
  - Frontend/demo: [Nomi frontend](https://crustaly.github.io/nomi/?utm_source=chatgpt.com)  
  Very useful if you want to study how a winning project combined **sensors + LLM reasoning + a polished UI**, instead of making a pure software agent. 

- **Leavitt — autonomous SRE / incident investigation**
  - GitHub: [github.com/msradam/leavitt](https://github.com/msradam/leavitt?utm_source=chatgpt.com)
  - Supporting runtime: [github.com/msradam/theodosia](https://github.com/msradam/theodosia?utm_source=chatgpt.com)
  - Devpost: [Leavitt on Devpost](https://devpost.com/software/leavitt?utm_source=chatgpt.com)  
  This is a really good architecture reference because the agent doesn't just chat: it walks a constrained state machine and queries Prometheus, Loki, k6, feature flags, etc. 

- **HydroClawNics — autonomous hydroponic farm**
  - GitHub: [github.com/GalexY727/Hydroclawnics](https://github.com/GalexY727/Hydroclawnics?utm_source=chatgpt.com)
  - Devpost: [HydroClawNics on Devpost](https://devpost.com/software/hydroclawnics?utm_source=chatgpt.com)  
  Strong reference for **IoT / cyber-physical agent** ideas. Nemotron reads sensor values like temperature, pH, EC, humidity and water level, then calls control functions. 

- **MonkeyClaw — autonomous security/red-team agent**
  - Devpost: [MonkeyClaw on Devpost](https://devpost.com/software/monkeyclaw?utm_source=chatgpt.com)
  - The Devpost page links its GitHub under “Try it out”; that page is the safest starting point because search results didn’t expose the exact repo URL cleanly.
  
  This one is probably the most sophisticated winner I found. Its loop is basically **red → judge → reproduce → patch → verify**, with Nemotron used across multiple roles. 

- **FactoryMind — warehouse/factory digital twin**
  - Devpost: [FactoryMind on Devpost](https://devpost.com/software/factorymind?utm_source=chatgpt.com)
  - GitHub is linked directly from its Devpost “Try it out” section.
  
  It combines a Python simulation backend, Three.js digital twin, Nemotron Super/Nano, NIM endpoints and autonomous worker/leader agents. Very relevant if you're considering **simulation + optimization + agent actions**. 

- **Syscall — AI CUDA / performance engineer**
  - Devpost: [Syscall on Devpost](https://devpost.com/software/syscall?utm_source=chatgpt.com)
  - GitHub is linked under “Try it out” on the Devpost submission.
  
  Stack includes **Nemotron 3 Nano, NVCC, NVRTC, NVIDIA NIM, Stanford KernelBench, MCP, SSH, benchmarking and source manipulation**. If you want to understand what NVIDIA judges consider a compelling developer-tool project, study this one closely. 

- **Skyrchitect — cloud architecture agent**
  - Devpost: [Skyrchitect on Devpost](https://devpost.com/software/skyrchitect-6d37yf?utm_source=chatgpt.com)
  - GitHub is linked from its Devpost submission.
  
  The interesting bit is its workflow: requirements → retrieval → service selection → architecture generation → cost estimation → infrastructure code. Nemotron Nano + NVIDIA embedding NIM + SageMaker. 

- **Agent Builder — generates deployable agent workflows**
  - Devpost: [Agent Builder on Devpost](https://devpost.com/software/agent-builder?utm_source=chatgpt.com)
  - NVIDIA/AWS winners page: [AWS + NVIDIA hackathon winners](https://nvidia-aws.devpost.com/updates/38660-and-the-winner-is?utm_source=chatgpt.com)
  
  Good reference for UI/UX: natural language → agent graph → tools → workflow → deployment. 

- **Toyotron — multi-agent automotive concierge**
  - Devpost: [Toyotron on Devpost](https://devpost.com/software/toyotron?utm_source=chatgpt.com)
  - Live app: [Toyotron demo](https://toyota-toyotron.vercel.app?utm_source=chatgpt.com)
  - GitHub is linked from the Devpost page.
  
  This is worth studying less for deep infrastructure and more for **hackathon polish**: recommendations, phone interaction, scheduling, email, multi-agent orchestration and a coherent end-to-end demo. It won both the NVIDIA and Toyota tracks at HackUTD. 

If you only inspect **five codebases/submissions**, I'd do:

1. **Detour** — best physical-world/edge architecture.
2. **Leavitt** — best tool-using agent architecture.
3. **Syscall** — best NVIDIA/dev-tool angle.
4. **HydroClawNics** — best sensor → reasoning → action loop.
5. **MonkeyClaw** — best complex multi-agent verification loop.

Those five give you very different versions of the same winning pattern: **the model has a real environment, specialized tools, observable state, and a feedback loop** rather than merely producing text.
