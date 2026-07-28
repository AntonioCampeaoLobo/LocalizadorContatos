# -*- coding: utf-8 -*-
"""
tests/test_extracao.py
======================

Testes das rotinas críticas de confiabilidade.

Estas funções decidem o que entra na planilha do cliente. Um falso positivo
aqui grava o telefone errado em um cadastro real — por isso a bateria enfatiza
os casos que **devem ser rejeitados** tanto quanto os que devem passar.

Execução::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Permite executar os testes a partir de qualquer diretório.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config       # noqa: E402
import utils        # noqa: E402
from modelos import (  # noqa: E402
    Confianca,
    Empresa,
    Evidencia,
    Fonte,
    ResultadoPesquisa,
    StatusPesquisa,
)


class TestTelefones(unittest.TestCase):
    """Extração, validação e formatação de telefones brasileiros."""

    def test_formatos_comuns(self):
        texto = (
            "Ligue para (19) 3824-9898 ou 19 99844-3483. "
            "Também atendemos no +55 11 4004-1234."
        )
        digitos = [t.digitos for t in utils.extrair_telefones(texto)]
        self.assertIn("1938249898", digitos)
        self.assertIn("19998443483", digitos)
        self.assertIn("1140041234", digitos)

    def test_formatacao(self):
        self.assertEqual(utils.formatar_telefone("1938249898"), "(19) 3824-9898")
        self.assertEqual(utils.formatar_telefone("19998443483"), "(19) 99844-3483")
        self.assertEqual(utils.formatar_telefone("5519998443483"), "(19) 99844-3483")

    def test_prefixo_interurbano_zero(self):
        """Formato legado das carteiras de clientes: '019 38429898'."""
        digitos = {t.digitos for t in utils.extrair_telefones("019 38429898")}
        self.assertEqual(digitos, {"1938429898"})

        digitos = {t.digitos for t in utils.extrair_telefones("019 989443483")}
        self.assertEqual(digitos, {"19989443483"})

        digitos = {t.digitos for t in utils.extrair_telefones("(019) 3225-8238")}
        self.assertEqual(digitos, {"1932258238"})

    def test_numero_sem_separador(self):
        """Exportações de sistema gravam tudo junto."""
        digitos = {t.digitos for t in utils.extrair_telefones("Tel 1938249898")}
        self.assertEqual(digitos, {"1938249898"})

        digitos = {t.digitos for t in utils.extrair_telefones("5519998443483")}
        self.assertEqual(digitos, {"19998443483"})

    def test_solido_nao_confunde_com_cnpj_sem_mascara(self):
        """CNPJ tem 14 dígitos — não pode ser lido como telefone."""
        self.assertEqual(utils.extrair_telefones("CNPJ 11222333000181"), [])

    def test_rejeita_cnpj(self):
        """Um CNPJ no rodapé não pode virar telefone."""
        texto = "CNPJ: 12.345.678/0001-95 — Todos os direitos reservados."
        self.assertEqual(utils.extrair_telefones(texto), [])

    def test_rejeita_cep_e_datas(self):
        self.assertEqual(utils.extrair_telefones("CEP 13.170-000"), [])
        self.assertEqual(utils.extrair_telefones("Fundada em 12/03/1998"), [])

    def test_rejeita_ddd_invalido(self):
        """DDD 00 e 90 não existem no plano da Anatel."""
        self.assertFalse(utils.telefone_valido("0038249898"))
        self.assertFalse(utils.telefone_valido("9038249898"))

    def test_rejeita_prefixo_invalido(self):
        # Fixo não começa com 1, 6, 7, 8 ou 9.
        self.assertFalse(utils.telefone_valido("1918249898"))
        # Celular de 11 dígitos precisa começar com 9.
        self.assertFalse(utils.telefone_valido("19888443483"))

    def test_rejeita_repetidos(self):
        self.assertFalse(utils.telefone_valido("19999999999"))
        self.assertFalse(utils.telefone_valido("1900000000"))

    def test_numero_sem_ddd_isolado_e_ignorado(self):
        """Sem DDD e sem número vizinho, não há como saber a região."""
        self.assertEqual(utils.extrair_telefones("Telefone: 3824-9898"), [])

    def test_heranca_de_ddd(self):
        """"(19) 3824-9898 / 99844-3483" -> ambos com DDD 19."""
        texto = "(19) 3824-9898 / 99844-3483"
        extraidos = utils.extrair_telefones(texto, herdar_ddd=True)
        digitos = {t.digitos for t in extraidos}
        self.assertEqual(digitos, {"1938249898", "19998443483"})
        herdado = next(t for t in extraidos if t.digitos == "19998443483")
        self.assertTrue(herdado.ddd_herdado)

    def test_heranca_nao_atravessa_texto_longo(self):
        """Números separados por parágrafos não devem compartilhar DDD."""
        texto = (
            "(19) 3824-9898. Nossa empresa atua no mercado brasileiro desde "
            "mil novecentos e noventa oferecendo servicos diferenciados. 3824-1111"
        )
        digitos = {t.digitos for t in utils.extrair_telefones(texto, herdar_ddd=True)}
        self.assertEqual(digitos, {"1938249898"})

    def test_zero_oitocentos(self):
        digitos = {t.digitos for t in utils.extrair_telefones("Central 0800 771 2233")}
        self.assertIn("08007712233", digitos)

    def test_ddd_coerente_com_uf(self):
        self.assertTrue(utils.ddd_coerente_com_uf("1938249898", "SP"))
        self.assertFalse(utils.ddd_coerente_com_uf("4138249898", "SP"))

    def test_ddd_por_cidade_e_mais_rigoroso_que_por_uf(self):
        """
        Um (11) da capital passa na checagem por UF para uma empresa de Amparo,
        mas Amparo é DDD 19 — a checagem por cidade pega o que a por UF deixa.
        """
        self.assertTrue(utils.ddd_coerente_com_uf("1155369432", "SP"))
        self.assertFalse(
            utils.ddd_coerente_com_local("1155369432", "AMPARO", "SP")
        )
        self.assertTrue(
            utils.ddd_coerente_com_local("1938072031", "AMPARO", "SP")
        )

    def test_cidade_fora_do_mapa_cai_para_checagem_por_uf(self):
        self.assertIsNone(utils.ddd_da_cidade("CIDADE INEXISTENTE"))
        self.assertTrue(
            utils.ddd_coerente_com_local("1155369432", "CIDADE INEXISTENTE", "SP")
        )
        self.assertFalse(
            utils.ddd_coerente_com_local("4138249898", "CIDADE INEXISTENTE", "SP")
        )

    def test_cidades_da_carteira_mapeadas(self):
        esperados = {
            "CAMPINAS": 19, "AMPARO": 19, "COSMOPOLIS": 19, "PAULINIA": 19,
            "JUNDIAI": 11, "ATIBAIA": 11, "CERQUILHO": 15, "ARARAQUARA": 16,
            "VARGEM GRANDE PAULISTA": 11,
        }
        for cidade, ddd in esperados.items():
            self.assertEqual(utils.ddd_da_cidade(cidade), ddd, cidade)


class TestEmails(unittest.TestCase):
    """Extração e validação de e-mails."""

    def test_extracao_simples(self):
        texto = "Contato: financeiro@fyp.com.br ou vendas@empresa.ind.br"
        self.assertEqual(
            utils.extrair_emails(texto),
            ["financeiro@fyp.com.br", "vendas@empresa.ind.br"],
        )

    def test_rejeita_arquivo_como_email(self):
        self.assertEqual(utils.extrair_emails("<img src='logo@2x.png'>"), [])

    def test_rejeita_dominios_de_plataforma(self):
        for email in (
            "a@sentry.wixpress.com",
            "x@example.com",
            "b@schema.org",
            "c@seudominio.com.br",
        ):
            self.assertFalse(utils.email_valido(email), email)

    def test_email_ofuscado(self):
        texto = "Escreva para contato (arroba) marsonmateriais (ponto) com (ponto) br"
        self.assertIn("contato@marsonmateriais.com.br", utils.extrair_emails(texto))

    def test_dominio_placeholder_e_bloqueado(self):
        """'empresa.com.br' e afins são exemplos de template, não contatos reais."""
        texto = "Escreva para contato (arroba) empresa (ponto) com (ponto) br"
        self.assertEqual(utils.extrair_emails(texto), [])


class TestWhatsApp(unittest.TestCase):
    """Só links declarados contam como WhatsApp."""

    def test_link_wa_me(self):
        html = '<a href="https://wa.me/5519998443483">Fale conosco</a>'
        self.assertEqual(utils.extrair_whatsapps(html), ["(19) 99844-3483"])

    def test_api_whatsapp(self):
        html = 'href="https://api.whatsapp.com/send?phone=551938249898&text=oi"'
        self.assertEqual(utils.extrair_whatsapps(html), ["(19) 3824-9898"])

    def test_numero_solto_nao_vira_whatsapp(self):
        self.assertEqual(utils.extrair_whatsapps("WhatsApp (19) 99844-3483"), [])


class TestCNPJ(unittest.TestCase):
    """Validação por dígito verificador."""

    def test_cnpj_valido(self):
        self.assertTrue(utils.cnpj_valido("11.222.333/0001-81"))

    def test_cnpj_invalido(self):
        self.assertFalse(utils.cnpj_valido("11.222.333/0001-99"))
        self.assertFalse(utils.cnpj_valido("00.000.000/0000-00"))

    def test_extracao(self):
        texto = "Inscrita no CNPJ 11.222.333/0001-81, com sede em Campinas."
        self.assertEqual(utils.extrair_cnpjs(texto), ["11222333000181"])

    def test_formatacao(self):
        self.assertEqual(utils.formatar_cnpj("11222333000181"), "11.222.333/0001-81")


class TestNomeMatcher(unittest.TestCase):
    """Comparação de razões sociais — o guardião contra homônimos."""

    def setUp(self):
        self.m = utils.NomeMatcher()

    def test_ignora_sufixos_societarios(self):
        self.assertEqual(
            self.m.normalizar_razao("ANCONA TRANSPORTES LTDA ME"), "ancona transportes"
        )
        self.assertEqual(
            self.m.normalizar_razao("A.R. MARSON MATERIAIS EIRELI"), "a r marson materiais"
        )

    def test_mesma_empresa_com_variacao(self):
        aceito, score = self.m.compativel(
            "AMPARO VIACAO E TURISMO LTDA", "Amparo Viação e Turismo"
        )
        self.assertTrue(aceito)
        self.assertGreater(score, 0.9)

    def test_homonimos_de_ramos_diferentes_nao_batem(self):
        """ANCONA BUFFET e ANCONA TRANSPORTES são empresas distintas."""
        aceito, _ = self.m.compativel(
            "ANCONA BUFFET LTDA EPP", "ANCONA TRANSPORTES LTDA ME"
        )
        self.assertFalse(aceito)

    def test_empresas_totalmente_diferentes(self):
        aceito, score = self.m.compativel(
            "ZANCA TRANSPORTES LTDA", "AGRO PECUARIA PEETERS S/A"
        )
        self.assertFalse(aceito)
        self.assertLess(score, config.SIMILARIDADE_MINIMA_DUVIDA)

    def test_nome_truncado_na_planilha(self):
        """A planilha corta nomes em 40 caracteres; isso não pode reprovar."""
        aceito, score = self.m.compativel(
            "ALAMEDAS OURO VERDE EMPREENDIMENTOS IMOB",
            "Alamedas Ouro Verde Empreendimentos Imobiliários Ltda",
        )
        self.assertTrue(aceito)
        self.assertGreater(score, 0.85)

    def test_tokens_distintivos_ignoram_palavras_de_ramo(self):
        self.assertEqual(
            self.m.tokens_distintivos("A1 TRANSPORTES E LOGISTICA LTDA ME"), {"a1"}
        )

    def test_nomes_identicos_sem_token_distintivo_sao_aceitos(self):
        """
        "A & A EXECUTIVA TRANSPORTES" só tem palavras genéricas, mas coincidir
        exatamente com o registro da Receita Federal é confirmação legítima.
        """
        self.assertEqual(
            self.m.tokens_distintivos("A & A EXECUTIVA TRANSPORTES LTDA - ME"), set()
        )
        aceito, score = self.m.compativel(
            "A & A EXECUTIVA TRANSPORTES LTDA - ME", "A & A EXECUTIVA TRANSPORTES LTDA"
        )
        self.assertTrue(aceito)
        self.assertEqual(score, 1.0)

    def test_exige_token_distintivo_em_comum(self):
        """Só palavras genéricas em comum nunca aprova a correspondência."""
        aceito, _ = self.m.compativel(
            "SILVA COMERCIO DE MATERIAIS", "PEREIRA COMERCIO DE MATERIAIS"
        )
        self.assertFalse(aceito)


class TestDominios(unittest.TestCase):
    """Classificação de URLs."""

    def test_dominio_base(self):
        self.assertEqual(utils.dominio_base("https://www.abc.com.br/contato"), "abc.com.br")
        self.assertEqual(utils.dominio_base("http://loja.abc.com.br"), "abc.com.br")
        self.assertEqual(utils.dominio_base("https://abc.io/x"), "abc.io")

    def test_site_oficial_descarta_agregadores(self):
        for url in (
            "https://www.facebook.com/empresa",
            "https://br.linkedin.com/company/empresa",
            "https://cnpj.biz/11222333000181",
            "https://www.telelistas.net/empresa",
        ):
            self.assertFalse(utils.pode_ser_site_oficial(url), url)

    def test_site_proprio_e_aceito(self):
        self.assertTrue(utils.pode_ser_site_oficial("https://www.marsonmateriais.com.br"))


class TestModeloConfiabilidade(unittest.TestCase):
    """A regra estrutural: nenhum dado sem evidência."""

    def setUp(self):
        self.empresa = Empresa(linha=2, razao_social="ACME LTDA", cidade="CAMPINAS")
        self.resultado = ResultadoPesquisa(empresa=self.empresa)
        self.evidencia = Evidencia(
            fonte=Fonte.SITE_OFICIAL, url="https://acme.com.br/contato"
        )

    def test_evidencia_exige_url(self):
        with self.assertRaises(ValueError):
            Evidencia(fonte=Fonte.SITE_OFICIAL, url="")

    def test_telefone_registrado_com_origem(self):
        self.assertTrue(
            self.resultado.adicionar_telefone("(19) 3824-9898", self.evidencia)
        )
        dado = self.resultado.telefones[0]
        self.assertEqual(dado.evidencia.url, "https://acme.com.br/contato")
        self.assertEqual(dado.confianca, Confianca.ALTA)

    def test_deduplicacao_e_promocao_de_confianca(self):
        fraca = Evidencia(fonte=Fonte.CATALOGO, url="https://catalogo.x/acme")
        self.resultado.adicionar_telefone("(19) 3824-9898", fraca)
        self.assertEqual(self.resultado.telefones[0].confianca, Confianca.BAIXA)

        # Mesmo número por fonte melhor: não duplica e promove a confiança.
        adicionado = self.resultado.adicionar_telefone("(19) 3824-9898", self.evidencia)
        self.assertFalse(adicionado)
        self.assertEqual(len(self.resultado.telefones), 1)
        self.assertEqual(self.resultado.telefones[0].confianca, Confianca.ALTA)

    def test_confianca_baixa_nao_vai_para_a_planilha(self):
        fraca = Evidencia(fonte=Fonte.CATALOGO, url="https://catalogo.x/acme")
        self.resultado.adicionar_telefone("(19) 3824-9898", fraca)
        self.assertEqual(self.resultado.celula_contato(), "")
        self.assertFalse(self.resultado.tem_telefone_preenchivel)

    def test_multiplos_telefones_em_linhas_separadas(self):
        self.resultado.adicionar_telefone("(19) 3824-9898", self.evidencia)
        self.resultado.adicionar_telefone("(19) 99844-3483", self.evidencia)
        self.assertEqual(
            self.resultado.telefones_texto(), "(19) 3824-9898\n(19) 99844-3483"
        )

    def test_prioridade_de_fonte(self):
        self.assertLess(Fonte.SITE_OFICIAL.prioridade, Fonte.GOOGLE_BUSINESS.prioridade)
        self.assertLess(Fonte.GOOGLE_BUSINESS.prioridade, Fonte.CATALOGO.prioridade)
        self.assertEqual(Fonte.RECEITA_FEDERAL.confianca, Confianca.ALTA)
        self.assertEqual(Fonte.LINKEDIN.confianca, Confianca.MEDIA)
        self.assertEqual(Fonte.CATALOGO.confianca, Confianca.BAIXA)

    def test_cores_por_status(self):
        self.assertEqual(StatusPesquisa.ENCONTRADO.cor_linha, config.COR_VERDE_CLARO)
        self.assertEqual(StatusPesquisa.APENAS_EMAIL.cor_linha, config.COR_AMARELO)
        self.assertEqual(StatusPesquisa.NAO_ENCONTRADO.cor_linha, config.COR_VERMELHO)
        self.assertEqual(StatusPesquisa.REVISAO_MANUAL.cor_linha, config.COR_LARANJA)


class TestValidacaoDeSite(unittest.TestCase):
    """
    Regressão do falso positivo real encontrado em teste de campo.

    "A & A EXECUTIVA TRANSPORTES LTDA - ME" (Cosmópolis/SP) casou com
    ``executiva.com.br`` — uma empresa do Paraná — porque seu único token
    distintivo é a palavra comum "executiva". O e-mail da prefeitura de
    Cosmópolis também foi capturado da mesma página.
    """

    def setUp(self):
        import importlib.util

        caminho = Path(__file__).resolve().parent.parent / "site.py"
        especificacao = importlib.util.spec_from_file_location("localizador_site", caminho)
        modulo = importlib.util.module_from_spec(especificacao)
        sys.modules["localizador_site"] = modulo
        especificacao.loader.exec_module(modulo)
        self.mod_site = modulo
        self.rastreador = modulo.RastreadorSite(cliente=None)

    def test_nome_so_com_palavras_genericas_e_rejeitado(self):
        """'A & A EXECUTIVA TRANSPORTES' não tem nenhum token identificador."""
        empresa = Empresa(
            linha=3,
            razao_social="A & A EXECUTIVA TRANSPORTES LTDA - ME",
            cidade="COSMOPOLIS",
        )
        self.assertEqual(
            utils.matcher.tokens_distintivos(empresa.razao_social), set()
        )
        pagina = (
            "Executiva Transportes — viagens executivas. Atendemos Cosmópolis, "
            "Curitiba e região. Telefone (41) 3668-7782."
        )
        confirmado, confianca, motivo = self.rastreador._validar_identidade(
            empresa, "https://www.executiva.com.br", pagina, Confianca.ALTA
        )
        self.assertFalse(confirmado, motivo)
        self.assertEqual(confianca, Confianca.BAIXA)

    def test_homonimos_nao_compartilham_o_mesmo_site(self):
        """
        Regressão do erro mais grave encontrado em produção.

        ANCONA BUFFET e ANCONA TRANSPORTES, ambas de Amparo, receberam contatos
        idênticos de ``ancona.app.br`` — um site da capital (DDD 11). O domínio
        contém o token "ancona" das duas, mas não cobre o nome inteiro de
        nenhuma delas.
        """
        pagina = (
            "Ancona — soluções em eventos. São Paulo/SP. "
            "Telefone (11) 5536-9432. WhatsApp (11) 94009-7785."
        )
        for razao in ("ANCONA BUFFET LTDA EPP", "ANCONA TRANSPORTES LTDA ME"):
            empresa = Empresa(linha=18, razao_social=razao, cidade="AMPARO")
            confirmado, confianca, motivo = self.rastreador._validar_identidade(
                empresa, "https://ancona.app.br/", pagina, Confianca.ALTA
            )
            self.assertFalse(confirmado, f"{razao}: {motivo}")
            self.assertEqual(confianca, Confianca.BAIXA)
            self.assertIn("homônima", motivo)

    def test_dominio_que_cobre_o_nome_inteiro_e_aceito(self):
        """O mesmo mecanismo não pode reprovar um domínio legítimo."""
        empresa = Empresa(
            linha=5, razao_social="A.R. MARSON MATERIAIS EIRELI", cidade="COSMOPOLIS"
        )
        pagina = "A.R. Marson Materiais para Construção. Telefone (19) 3812-3043."
        confirmado, confianca, _ = self.rastreador._validar_identidade(
            empresa, "https://marsonmateriais.com.br", pagina, Confianca.ALTA
        )
        self.assertTrue(confirmado)
        self.assertEqual(confianca, Confianca.MEDIA)

    def test_cobertura_do_dominio(self):
        casos = [
            ("ANCONA BUFFET LTDA EPP", "ancona", 0.5),
            ("ANCONA TRANSPORTES LTDA ME", "ancona", 0.5),
            ("A.R. MARSON MATERIAIS EIRELI", "marsonmateriais", 1.0),
            ("ZANCA TRANSPORTES LTDA", "zancatransportes", 1.0),
        ]
        for razao, nucleo, esperado in casos:
            empresa = Empresa(linha=1, razao_social=razao)
            self.assertAlmostEqual(
                self.rastreador._cobertura_do_dominio(empresa, nucleo), esperado,
                places=2, msg=razao,
            )

    def test_token_unico_exige_presenca_no_dominio(self):
        empresa = Empresa(
            linha=5, razao_social="A.R. MARSON MATERIAIS EIRELI", cidade="COSMOPOLIS"
        )
        pagina = "Loja de materiais. Citamos o fornecedor Marson. Cosmópolis/SP."
        confirmado, _, motivo = self.rastreador._validar_identidade(
            empresa, "https://outraempresa.com.br", pagina, Confianca.ALTA
        )
        self.assertFalse(confirmado, motivo)
        self.assertIn("domínio", motivo)

    def test_dois_tokens_mais_cidade_confirmam(self):
        empresa = Empresa(
            linha=5, razao_social="A.R. MARSON MATERIAIS EIRELI", cidade="COSMOPOLIS"
        )
        pagina = (
            "A.R. Marson Materiais para Construção — Rua 24 de Maio, Cosmópolis/SP. "
            "Telefone (19) 3812-3043."
        )
        confirmado, confianca, _ = self.rastreador._validar_identidade(
            empresa, "https://marsonmateriais.com.br", pagina, Confianca.ALTA
        )
        self.assertTrue(confirmado)
        self.assertEqual(confianca, Confianca.ALTA)

    def test_sem_cidade_a_confianca_cai_para_media(self):
        empresa = Empresa(
            linha=5, razao_social="A.R. MARSON MATERIAIS EIRELI", cidade="COSMOPOLIS"
        )
        pagina = "A.R. Marson Materiais para Construção. Telefone (19) 3812-3043."
        confirmado, confianca, _ = self.rastreador._validar_identidade(
            empresa, "https://marsonmateriais.com.br", pagina, Confianca.ALTA
        )
        self.assertTrue(confirmado)
        self.assertEqual(confianca, Confianca.MEDIA)

    def test_cnpj_na_pagina_confirma_qualquer_nome(self):
        empresa = Empresa(
            linha=3, razao_social="A & A EXECUTIVA TRANSPORTES", cidade="COSMOPOLIS"
        )
        empresa.cnpj = "11222333000181"
        pagina = "Empresa X — CNPJ 11.222.333/0001-81."
        confirmado, confianca, motivo = self.rastreador._validar_identidade(
            empresa, "https://qualquer.com.br", pagina, Confianca.ALTA
        )
        self.assertTrue(confirmado)
        self.assertEqual(confianca, Confianca.ALTA)
        self.assertIn("CNPJ", motivo)

    def test_email_de_terceiro_e_descartado(self):
        """O e-mail da prefeitura não pode entrar como contato da empresa."""
        self.assertFalse(
            self.rastreador._email_pertence_ao_site(
                "comunicacao@cosmopolis.sp.gov.br", "https://www.executiva.com.br/contato"
            )
        )

    def test_email_do_proprio_dominio_e_aceito(self):
        self.assertTrue(
            self.rastreador._email_pertence_ao_site(
                "contato@executiva.com.br", "https://www.executiva.com.br/contato"
            )
        )

    def test_email_de_provedor_gratuito_e_aceito(self):
        self.assertTrue(
            self.rastreador._email_pertence_ao_site(
                "marsonmateriais@gmail.com", "https://marsonmateriais.com.br/contato"
            )
        )


class TestConsultasGeradas(unittest.TestCase):
    """
    As consultas precisam incluir a cidade e não podem conter iniciais soltas.

    Regressão: ``"A. B. CHISTELLI COMERCIAL"`` fazia o buscador devolver
    páginas sobre a letra "B" (Brasileirão Série B, Wikipédia) em vez da
    empresa — a etapa de CNPJ ficava sem nenhum candidato.
    """

    def test_nome_busca_remove_iniciais(self):
        empresa = Empresa(linha=4, razao_social="A. B. CHISTELLI COMERCIAL")
        self.assertEqual(empresa.nome_busca(), "CHISTELLI COMERCIAL")

    def test_nome_busca_remove_iniciais_coladas(self):
        empresa = Empresa(linha=5, razao_social="A.R. MARSON MATERIAIS EIRELI")
        self.assertEqual(empresa.nome_busca(), "MARSON MATERIAIS")

    def test_nome_busca_remove_forma_societaria(self):
        empresa = Empresa(linha=7, razao_social="A1 TRANSPORTES E LOGISTICA LTDA ME")
        self.assertEqual(empresa.nome_busca(), "A1 TRANSPORTES LOGISTICA")

    def test_nome_busca_preserva_nome_sem_iniciais(self):
        empresa = Empresa(linha=8, razao_social="ZANCA TRANSPORTES LTDA")
        self.assertEqual(empresa.nome_busca(), "ZANCA TRANSPORTES")

    def test_nome_busca_nao_esvazia_nome_curto(self):
        """Se a limpeza consumir tudo, o original é mantido."""
        empresa = Empresa(linha=9, razao_social="A & A LTDA")
        self.assertEqual(empresa.nome_busca(), "A & A LTDA")

    def test_consulta_inclui_cidade(self):
        empresa = Empresa(
            linha=4, razao_social="A. B. CHISTELLI COMERCIAL", cidade="SUMARE"
        )
        consultas = empresa.consultas()
        self.assertTrue(any("SUMARE" in c and "telefone" in c for c in consultas))
        self.assertTrue(any("contato" in c for c in consultas))
        # Nenhuma consulta pode começar com uma inicial solta.
        for consulta in consultas:
            self.assertNotIn("A. B.", consulta)

    def test_consultas_cnpj(self):
        empresa = Empresa(linha=5, razao_social="ACME COMERCIAL LTDA", cidade="CAMPINAS")
        consultas = empresa.consultas_cnpj()
        self.assertTrue(all("CNPJ" in c or "cnpj" in c for c in consultas))
        self.assertTrue(all("CAMPINAS" in c for c in consultas))
        self.assertGreaterEqual(len(consultas), 3)
        self.assertEqual(len(consultas), len(set(consultas)))


class TestMotoresBusca(unittest.TestCase):
    """
    Parsers dos buscadores, testados offline com fixtures.

    Necessário porque os buscadores bloqueiam por IP sob uso sustentado — em
    teste de campo o DuckDuckGo passou a recusar conexão TCP. Sem estes testes,
    um erro de parsing seria indistinguível de um bloqueio.
    """

    def setUp(self):
        import importlib.util

        caminho = Path(__file__).resolve().parent.parent / "google.py"
        especificacao = importlib.util.spec_from_file_location(
            "localizador_google", caminho
        )
        modulo = importlib.util.module_from_spec(especificacao)
        sys.modules["localizador_google"] = modulo
        especificacao.loader.exec_module(modulo)
        self.g = modulo
        self.motor = modulo.DuckDuckGoHTML(cliente=None)

    def test_extrai_endpoint_html(self):
        fixture = """
        <div class="result results_links">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcnpj.biz%2F07333796000143">
            Adriano Marson Casa e Construcao
          </a>
          <a class="result__snippet">Telefone (19) 3812-3043 em Cosmópolis</a>
        </div>
        """
        resultados = self.motor._extrair_html(fixture, 5)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0].url, "https://cnpj.biz/07333796000143")
        self.assertIn("Marson", resultados[0].titulo)
        self.assertIn("3812-3043", resultados[0].snippet)

    def test_extrai_endpoint_lite(self):
        """O endpoint lite usa tabela: link em uma linha, trecho na seguinte."""
        fixture = """
        <table>
          <tr><td><a class="result-link" href="https://cnpj.biz/07333796000143">
              A.R. Marson Materiais Ltda</a></td></tr>
          <tr><td class="result-snippet">CNPJ 07.333.796/0001-43 — Cosmópolis/SP</td></tr>
          <tr><td><a class="result-link" href="https://cnpja.com/office/07333796000143">
              Cnpjá — A.R. Marson</a></td></tr>
          <tr><td class="result-snippet">Telefone (19) 3812-3043</td></tr>
        </table>
        """
        resultados = self.motor._extrair_lite(fixture, 5)
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0].url, "https://cnpj.biz/07333796000143")
        self.assertIn("07.333.796", resultados[0].snippet)
        self.assertEqual(resultados[1].url, "https://cnpja.com/office/07333796000143")

    def test_desembrulha_redirect_do_bing(self):
        """O Bing embrulha o destino em base64url com prefixo 'a1'."""
        import base64

        destino = "https://www.marsonmateriais.com.br/contato"
        codificado = base64.urlsafe_b64encode(destino.encode()).decode().rstrip("=")
        href = f"https://www.bing.com/ck/a?!&&p=abc&u=a1{codificado}"
        self.assertEqual(self.g.MotorBusca._limpar_url(href), destino)

    def test_desembrulha_redirect_do_duckduckgo(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com.br%2Fcontato&rut=x"
        self.assertEqual(
            self.g.MotorBusca._limpar_url(href), "https://exemplo.com.br/contato"
        )

    def test_classificacao_de_fonte_por_dominio(self):
        from modelos import Fonte

        casos = [
            ("https://br.linkedin.com/company/x", Fonte.LINKEDIN),
            ("https://cnpj.biz/123", Fonte.DIRETORIO),
            ("https://www.telelistas.net/x", Fonte.CATALOGO),
            ("https://brasilapi.com.br/api/cnpj/v1/1", Fonte.RECEITA_FEDERAL),
            ("https://marsonmateriais.com.br", Fonte.SITE_OFICIAL),
        ]
        for url, esperado in casos:
            item = self.g.ResultadoBusca(titulo="x", url=url)
            self.assertEqual(item.fonte_provavel(), esperado, url)


class TestDisjuntor(unittest.TestCase):
    """
    Disjuntor de fontes.

    Regressão: com o DuckDuckGo bloqueado por IP (timeout de conexão TCP, não
    erro HTTP), cada consulta gastava o timeout inteiro antes de cair para o
    Bing. Com ~5 consultas por empresa, a execução passou de 30s para mais de
    13 minutos por empresa.
    """

    def setUp(self):
        self.d = utils.Disjuntor("fonte_de_teste", limite=3, pausa=60)

    def test_fechado_no_inicio(self):
        self.assertFalse(self.d.aberto)

    def test_abre_apos_limite_de_falhas(self):
        self.d.registrar_falha()
        self.d.registrar_falha()
        self.assertFalse(self.d.aberto, "não deve abrir antes do limite")
        self.d.registrar_falha()
        self.assertTrue(self.d.aberto)

    def test_sucesso_zera_o_contador(self):
        self.d.registrar_falha()
        self.d.registrar_falha()
        self.d.registrar_sucesso()
        self.d.registrar_falha()
        self.assertFalse(self.d.aberto)

    def test_meio_aberto_apos_a_pausa(self):
        """Passada a pausa, uma requisição de teste é permitida."""
        curto = utils.Disjuntor("fonte_pausa_curta", limite=1, pausa=0)
        curto.registrar_falha()
        self.assertFalse(curto.aberto, "com pausa zero deve liberar o teste")

    def test_registro_compartilhado_por_nome(self):
        a = utils.Disjuntor.para("compartilhada")
        b = utils.Disjuntor.para("compartilhada")
        self.assertIs(a, b)

    def test_reiniciar_todos_fecha_os_disjuntores(self):
        d = utils.Disjuntor.para("sera_reiniciada")
        for _ in range(d.limite):
            d.registrar_falha()
        self.assertTrue(d.aberto)
        utils.Disjuntor.reiniciar_todos()
        self.assertFalse(d.aberto)


class TestCaptcha(unittest.TestCase):
    """Detecção de bloqueio anti-robô."""

    def test_detecta_marcadores(self):
        self.assertIsNotNone(
            utils.detectar_captcha("Our systems have detected unusual traffic")
        )
        self.assertIsNotNone(utils.detectar_captcha('<div class="g-recaptcha">'))

    def test_pagina_normal_passa(self):
        self.assertIsNone(
            utils.detectar_captcha("<html><body>Fale conosco: (19) 3824-9898</body></html>")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
