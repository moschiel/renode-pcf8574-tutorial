# Modelando um PCF8574 no Renode

O Renode permite executar firmware com modelos de hardware prontos. Quando um periférico não está disponível, é possível implementar seu comportamento em C# e conectá-lo à plataforma simulada.

Este tutorial cria um modelo do periférico **PCF8574, um expansor de oito entradas e saídas digitais controlado via I2C**. Ele permite ampliar os I/Os de um microcontrolador através do barramento I2C. O microcontrolador envia comandos para atuar nas saídas e lê o estado das entradas, como os LEDs e botões deste exemplo.

Para testar o caso de uso, o **PCF8574 é conectado ao I2C de um STM32F407**. O firmware alterna quatro LEDs pelas saídas P0..P3 e imprime na UART as mudanças dos botões ligados a P4..P7. Ao final, um painel web permite observar os LEDs e acionar os botões, sem placa física.


```text
STM32F407 --I2C1--> PCF8574 --P0..P3--> LEDs
                      ^
                      |
                   P4..P7
                   Botões

STM32 USART2 -----------------------> Terminal UART
```

## 1. Preparar o projeto

Requisitos: [Renode 1.16.1](https://renode.readthedocs.io/en/latest/introduction/installing.html), Python 3.10 ou superior e um editor. O Renode compila o modelo C# durante o carregamento; não é necessário instalar um SDK .NET separado. Os auxiliares usam apenas a biblioteca padrão do Python.

Baixe ou clone este repositório. Abra um terminal na raiz dele e crie uma pasta irmã chamada `meu-pcf8574`. Os comandos transferem apenas o firmware e os auxiliares de interface e testes; o modelo e a plataforma serão criados nas próximas etapas. Use outro nome se a pasta de destino já existir.

**Windows / PowerShell:**

```powershell
$referencia = (Get-Location).Path
New-Item -ItemType Directory ../meu-pcf8574 -ErrorAction Stop
Set-Location ../meu-pcf8574
New-Item -ItemType Directory models, platforms, scripts, firmware, tools, tests, web
Copy-Item "$referencia/firmware/demo.elf" firmware/
Copy-Item "$referencia/tools/renode_client.py" tools/
Copy-Item "$referencia/tools/lab.py" tools/
Copy-Item "$referencia/tests/check.py" tests/
Copy-Item "$referencia/scripts/bridge.py" scripts/
Copy-Item "$referencia/web/index.html" web/
python --version
```

<details>
<summary>Linux / Bash</summary>

```bash
referencia="$PWD"
mkdir ../meu-pcf8574
cd ../meu-pcf8574
mkdir models platforms scripts firmware tools tests web
cp "$referencia/firmware/demo.elf" firmware/
cp "$referencia/tools/renode_client.py" "$referencia/tools/lab.py" tools/
cp "$referencia/tests/check.py" tests/
cp "$referencia/scripts/bridge.py" scripts/
cp "$referencia/web/index.html" web/
python3 --version
```

Use `python3` no lugar de `python` nos próximos comandos, se necessário.

</details>

**Verificação:** `python --version` deve mostrar Python 3.10 ou superior; `python tools/lab.py --help` deve listar as opções `--monitor` e `--script`.

Todos os caminhos seguintes são relativos à pasta **meu-pcf8574**. Comandos identificados como **Terminal** são executados no PowerShell ou Bash; comandos do **Monitor** são executados dentro do Renode.

## 2. Implementar o PCF8574

O comportamento implementado segue este recorte do [datasheet TI PCF8574, revisão K](https://www.ti.com/lit/ds/symlink/pcf8574.pdf):

| Comportamento | Implementação | Referência |
| --- | --- | --- |
| Pinos inicialmente em nível alto | Latch inicia em `0xFF` | Seção 7.1 |
| Zero escrito força nível baixo | Zero no latch domina o nível observado | Figura 7-2 |
| Um escrito libera o pino com pull-up fraco | Sinal externo pode baixar o nível | Figura 7-2 |
| Leitura observa os pinos | Retornar o estado efetivo, não só o latch | Figura 7-4 |
| Atualização por bytes sucessivos | Processar cada byte recebido | Figura 7-3 |
| A2/A1/A0 em zero | Endereço I2C de sete bits `0x20` | Seção 7.3.3 |

Não há registrador de direção: escrever um permite usar o pino como entrada. Neste modelo digital, o nível observado é `outputLatch & externalLevels`. Correntes e resistências não são simuladas.

**Nota:** para bytes sucessivos, o modelo segue a figura 7-3; o texto da seção 7.3.1 diverge desse diagrama. O firmware deste exemplo envia um byte por transação.

Crie `models/PCF8574.cs`:

<!-- tutorial-file: models/PCF8574.cs -->
```csharp
using System;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Tutorial
{
    public class PCF8574 : II2CPeripheral, IGPIOReceiver, INumberedGPIOOutput
    {
        public PCF8574()
        {
            var pins = new Dictionary<int, IGPIO>();
            for(var pin = 0; pin < 8; pin++)
            {
                pins.Add(pin, new GPIO());
            }
            Connections = pins;
            Reset();
        }

        public IReadOnlyDictionary<int, IGPIO> Connections { get; }

        public void Reset()
        {
            outputLatch = 0xFF;
            UpdatePinLevels();
        }

        public void Write(byte[] data)
        {
            // Every data byte updates the port, not just the first byte.
            foreach(var value in data)
            {
                outputLatch = value;
                UpdatePinLevels();
            }
        }

        public byte[] Read(int count = 1)
        {
            var data = new byte[count];
            for(var i = 0; i < count; i++)
            {
                data[i] = EffectivePinLevels;
            }
            return data;
        }

        public void FinishTransmission()
        {
            // No register pointer or partial command to discard on STOP.
        }

        public void OnGPIO(int number, bool value)
        {
            if(number < 0 || number >= 8)
            {
                throw new ArgumentOutOfRangeException(nameof(number));
            }
            var mask = (byte)(1 << number);
            if(value)
            {
                externalLevels |= mask;
            }
            else
            {
                externalLevels &= (byte)~mask;
            }
            UpdatePinLevels();
        }

        private byte EffectivePinLevels => (byte)(outputLatch & externalLevels);

        private void UpdatePinLevels()
        {
            for(var pin = 0; pin < 8; pin++)
            {
                Connections[pin].Set((EffectivePinLevels & (1 << pin)) != 0);
            }
        }

        // Last command from the I2C master.
        private byte outputLatch;
        // External levels survive chip reset: a held button stays held.
        private byte externalLevels = 0xFF;
    }
}
```

`II2CPeripheral` atende o barramento I2C; `IGPIOReceiver` recebe mudanças externas; `INumberedGPIOOutput` expõe os oito fios de saída. Esses contratos separam o [comportamento do periférico](https://renode.readthedocs.io/en/latest/advanced/writing-peripherals.html) das conexões da plataforma.

O latch guarda o comando do mestre, enquanto `externalLevels` guarda o sinal externo. `OnGPIO` altera um bit desse sinal e `UpdatePinLevels` publica o resultado nos conectores. O reset não solta um botão que continua pressionado externamente.

**Verificação de compilação, no Terminal:**

```sh
python tools/lab.py --monitor --script models/PCF8574.cs
```

O Renode deve mostrar o carregamento de `PCF8574.cs` e retornar ao prompt `(monitor)`, sem erros de compilação. Digite `quit` para sair. A próxima etapa instancia o dispositivo para verificar leituras e escritas.

Se o executável não for encontrado, acrescente `--renode "C:/Program Files/Renode/renode.exe"` ao comando.

## 3. Conectar o modelo aos LEDs e botões

Crie `platforms/stm32.repl` para reutilizar a plataforma STM32F4 distribuída com o Renode:

<!-- tutorial-file: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"
```

Crie `platforms/pcf8574.repl`:

<!-- tutorial-file: platforms/pcf8574.repl -->
```text
pcf8574: Tutorial.PCF8574 @ i2c1 0x20
    preinit:
        include @models/PCF8574.cs
    0 -> led0@0
    1 -> led1@0
    2 -> led2@0
    3 -> led3@0

led0: Miscellaneous.LED @ sysbus
    invert: true
led1: Miscellaneous.LED @ sysbus
    invert: true
led2: Miscellaneous.LED @ sysbus
    invert: true
led3: Miscellaneous.LED @ sysbus
    invert: true

button4: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@4
button5: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@5
button6: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@6
button7: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@7
```

`Tutorial.PCF8574 @ i2c1 0x20` instancia a classe no controlador I2C1. O `preinit` carrega o C#; `0 -> led0@0` liga a saída zero à entrada do LED.

Os LEDs representam a ligação VCC, resistor, LED e pino do PCF: **nível baixo acende**, por isso `invert: true`. Nos botões, essa opção faz a pressão enviar zero e a liberação enviar um. `@ sysbus` registra esses objetos sem atribuir um endereço de memória.

Crie `scripts/platform.resc`, responsável por montar a sessão:

<!-- tutorial-file: scripts/platform.resc -->
```text
mach create "pcf8574-lab"
using sysbus
machine LoadPlatformDescription @platforms/stm32.repl
machine LoadPlatformDescription @platforms/pcf8574.repl
cpu PerformanceInMips 100
```

O `.repl` descreve os dispositivos e fios; o `.resc` executa comandos de configuração. Referência: [formato de plataformas do Renode](https://renode.readthedocs.io/en/latest/advanced/platform_description_format.html).

### Verificar leitura, escrita e fios

**Terminal:**

```sh
python tools/lab.py --monitor --script scripts/platform.resc
```

**Monitor**, em sequência:

<!-- tutorial-model-monitor -->
```text
cpu IsHalted true
python "from System import Array, Byte; dev = monitor.Machine['sysbus.i2c1.pcf8574']; print(list(dev.Read(1)))"
python "dev.Write(Array[Byte]([0xFE])); print(list(dev.Read(1)))"
sysbus.led0 State
sysbus.button4 Press
emulation RunFor "0.001"
python "print(list(dev.Read(1)))"
sysbus.button4 Release
emulation RunFor "0.001"
python "print(list(dev.Read(1)))"
```

| Consulta | Saída esperada |
| --- | --- |
| Leitura inicial | `[255]` = `0xFF` |
| Leitura após escrever `0xFE` | `[254]` = P0 baixo |
| Estado do LED0 | `True` = aceso |
| Leitura com P4 pressionado | `[238]` = `0xEE` |
| Leitura após soltar P4 | `[254]` = `0xFE` |

A CPU fica parada porque ainda não há firmware carregado. `RunFor` avança o tempo virtual para aplicar o evento do botão. Aqui as chamadas a `dev.Read` e `dev.Write` testam diretamente o modelo; a comunicação pelo controlador I2C será exercitada pelo firmware.

Digite `quit` antes da próxima etapa.

## 4. Executar o firmware STM32

O firmware fornecido foi gerado a partir do STM32CubeMX para a placa **STM32F407G-DISC1**, MCU **STM32F407VGTx**, com HAL. O projeto completo está em [firmware](firmware), com o [arquivo CubeMX](firmware/pcf8574-demo.ioc) e o [main.c](firmware/Core/Src/main.c).

A aplicação inicializa o port em `0xFF`, mantém P4..P7 liberados para entrada e alterna um LED a cada 250 ms, percorrendo P0..P3. A sequência acende P0, P1, P2, P3 e depois apaga P0, P1, P2, P3. Mudanças das entradas são impressas na USART2.

Toda escrita usa `0xF0 | ledLevels`, preservando as entradas liberadas. O firmware não escreve de volta os bits lidos dos botões, o que poderia prender uma entrada em zero. A HAL recebe o endereço `0x20 << 1`; no REPL ele permanece `0x20`.

### Sobrescrever a configuração do SysTick

O firmware configura SYSCLK e HCLK em **168 MHz**, enquanto a plataforma `stm32f4.repl` do Renode 1.16.1 define `systickFrequency` em **72 MHz**. É necessário alinhar esse parâmetro para que os intervalos calculados pelo firmware tenham a duração esperada na simulação.

Substitua o conteúdo de `platforms/stm32.repl` por:

<!-- tutorial-update: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"

nvic:
    systickFrequency: 168000000
```

O `using` importa a definição original; o bloco `nvic:` sobrescreve apenas o atributo indicado do dispositivo já declarado nessa definição. Os demais atributos são preservados, sem editar os arquivos da instalação do Renode.

Esse ajuste é aplicado durante a criação da plataforma. Encerre a sessão anterior e carregue uma nova após alterar o arquivo. Na seção 3, a CPU estava parada e o teste dos fios não dependia dessa frequência.

### Carregar o binário

Crie `scripts/demo.resc`:

<!-- tutorial-file: scripts/demo.resc -->
```text
include @scripts/platform.resc
sysbus LoadELF @firmware/demo.elf
showAnalyzer sysbus.usart2
```

### Verificar o firmware

**Terminal:**

```sh
python tools/lab.py --monitor
```

**Monitor**, em uma sessão nova e sem executar `start` antes:

<!-- tutorial-monitor -->
```text
emulation RunFor "0.270"
sysbus.led0 State
sysbus.button4 Press
emulation RunFor "0.100"
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
sysbus.button4 Release
emulation RunFor "0.100"
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
```

**Saída esperada:** LED0 em `True`; leituras `[238]` com P4 pressionado e `[254]` após soltar. Na UART, as mensagens de entrada devem passar por `INPUT P7..P4=0xF`, `0xE` e novamente `0xF`.

`RunFor` executa o intervalo de tempo virtual solicitado e para. Para execução contínua, use `start`; para interromper, `pause`. Saia com `quit`.

### Recompilar com STM32CubeIDE (opcional)

Importe a pasta `firmware` do repositório original em `File > Import > STM32CubeMX/STM32CubeIDE Project` e compile `pcf8574-demo` em `Debug`.

Na pasta `meu-pcf8574`, atualize o ELF:

```powershell
Copy-Item "$referencia/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf
```

No Bash: `cp "$referencia/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf`. Se reabriu o terminal, redefina `referencia` com o caminho do repositório original. Reinicie o Renode e repita a verificação anterior.

O ELF incluído foi compilado com Arm GCC 14.3 pelo auxiliar [build_firmware.py](tools/build_firmware.py). A importação gráfica no CubeIDE ainda não foi validada neste projeto.

## 5. Interagir pelo painel web

Encerre o Monitor e execute no **Terminal**:

```sh
python tools/lab.py
```

O painel abre em [localhost:8000](http://127.0.0.1:8000). Clique em um botão para pressionar e novamente para soltar. Os LEDs exibem os estados dos modelos do Renode; o terminal apresenta os caracteres enviados pelo firmware na USART2.

```text
Navegador --HTTP--> Python 3 --XML-RPC--> Renode
```

O painel é específico deste exemplo. Usa o [servidor remoto de testes do Renode](https://renode.readthedocs.io/en/latest/introduction/testing.html), compatível com Robot Framework, sem exigir sua instalação. `scripts/bridge.py` é executado pelo Python embutido do Renode para consultar os LEDs e capturar a UART; não deve ser executado diretamente com Python 3.

**Verificação sugerida:**

| Ação | Resultado esperado |
| --- | --- |
| Pressionar P4 | UART: `INPUT P7..P4=0xE` |
| Manter P4 e pressionar P7 | UART: `INPUT P7..P4=0x6` |
| Soltar ambos | UART termina em `INPUT P7..P4=0xF` |
| Pausar e avançar 250 ms | Um LED muda de estado por passo |

O efeito de um botão acionado enquanto pausado aparece no próximo avanço. Encerre com `Ctrl+C` no terminal.

## Testes automatizados (opcional)

Na pasta do projeto:

```sh
python tests/check.py all
```

São esperadas as linhas `PASS model:` e `PASS firmware:`. O primeiro teste verifica o modelo isolado; o segundo executa o firmware e verifica LEDs, botões e UART.

No repositório original, `python tests/check_tutorial.py` também reconstrói o exemplo a partir dos blocos deste README em uma pasta temporária e confere os comandos documentados.

## Limitações e diagnóstico

O modelo não implementa `INT`, correntes, curtos, temporização elétrica do I2C ou resolução de múltiplos drivers no mesmo pino. Trata-se de uma simulação funcional digital, não de equivalência elétrica completa ao CI.

| Problema | Ajuste |
| --- | --- |
| Renode não encontrado | Acrescente `--renode "caminho/do/executavel"` ao comando Python |
| Arquivo não encontrado | Execute na pasta `meu-pcf8574`; confira os nomes e extensões |
| Avisos de `flash_controller`, `rcc` ou `SYSCFG` no boot | Ocorrem nesta plataforma com a inicialização HAL; confira as leituras, o LED e a UART descritos na etapa 4 |
| Porta 8000 ocupada | Use `python tools/lab.py --port 8001` |
| Mudança no modelo ou ELF sem efeito | Encerre e abra uma nova sessão |
| Botão sem efeito | Avance o tempo virtual e confira se P4..P7 estão liberados |
| Tempo dos LEDs incorreto | Confira o SysTick de 168 MHz |

Validado no Windows com Renode 1.16.1. A execução no Linux e a importação gráfica no CubeIDE ainda requerem validação. Mantenha o painel local; não exponha as portas de controle do Renode à internet.
