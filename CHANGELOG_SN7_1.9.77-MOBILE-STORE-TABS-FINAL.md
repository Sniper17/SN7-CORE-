# SN7 Core 1.9.77 — Mobile Store Tabs Final

## Loja pública mobile
- Corrigida a navegação mobile da Loja pública para funcionar de fato entre as duas seções.
- **Itens** e **Áudios** agora são páginas/seções exclusivas, com apenas a seção selecionada visível.
- A barra inferior mobile mantém o mesmo padrão visual da barra do painel, com somente as opções **Itens** e **Áudios**.
- A versão desktop/PC da Loja pública permanece com o layout existente.

## Loading e vazios
- O skeleton inicial é exibido somente na seção mobile ativa, evitando blocos de carregamento aparecendo simultaneamente em uma seção vazia.
- Altura do skeleton mobile reduzida para evitar áreas gigantes durante o carregamento.
- Estados vazios continuam usando o mesmo componente visual da Loja.

## Compatibilidade
- Nenhuma alteração de banco ou de rotas da Loja.
- Nenhuma alteração intencional no painel administrativo da Loja.
- Pop-ups, confirmação de resgate, login e loader padrão da Loja foram preservados.
