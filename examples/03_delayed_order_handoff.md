# 03 · Pedido atrasado: verificação, desculpas e chamado

The customer asks about an order without its data, so Tom asks for the order number and the purchase e-mail. Order 8 was shipped with an estimated delivery on 28/02/2026, so it is late; with the customer's agreement Tom registers the case and gives the protocol.

- **Simulated date:** quarta-feira, 25/03/2026 às 10:30 (Campo Grande time), store open
- **Model:** groq · openai/gpt-oss-120b
- **Recorded:** 2026-11-03 with `scripts/record_conversations.py` (real agent output, not edited)
- **Input tokens:** 15,598 over 3 turns

## Turn 1

**Cliente:**

> Quero saber do meu pedido

**Tom:**

> Oi! Para eu consultar o seu pedido, preciso do número do pedido e do e‑mail usado na compra. Você pode me passar esses dados?

Tools called: none.

## Turn 2

**Cliente:**

> Pedido 8, anacarol.ferreira@coldmail.com

**Tom:**

> Oi Ana, vi que seu pedido já foi enviado, mas a entrega está atrasada há 25 dias. Posso abrir um chamado para a nossa equipe investigar e te devolver um protocolo de acompanhamento. Você confirma?

Tools called:
- `get_order_status` `{"email": "anacarol.ferreira@coldmail.com", "order_id": 8}`

## Turn 3

**Cliente:**

> Sim, pode registrar

**Tom:**

> Protocolo aberto: **EMP-20260325-0001**. A equipe vai analisar e retorna em até 24 horas úteis. Precisa de mais alguma coisa?

Tools called:
- `handoff_to_human` `{"contact": "anacarol.ferreira@coldmail.com", "order_id": 8, "reason": "delayed_order", "summary": "Cliente relata atraso de 25 dias no pedido 8, solicita abertura de chamado."}`
