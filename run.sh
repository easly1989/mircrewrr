#!/bin/bash
# MIRCrew Indexer Debug Tool - Helper Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colori
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_help() {
    echo "MIRCrew Indexer Debug Tool"
    echo ""
    echo "Uso: ./run.sh [comando]"
    echo ""
    echo "Comandi disponibili:"
    echo "  build       - Costruisce l'immagine Docker"
    echo "  run         - Esegue tutti i test"
    echo "  basic       - Test richiesta HTTP base"
    echo "  cloudscraper - Test con CloudScraper"
    echo "  login       - Test login"
    echo "  search      - Test ricerca (opzionale: query)"
    echo "  selenium    - Test con Selenium"
    echo "  shell       - Apre una shell nel container"
    echo "  proxy       - Avvia mitmproxy per analisi traffico"
    echo "  logs        - Mostra i log del container"
    echo "  clean       - Rimuove container e immagini"
    echo ""
    echo "Esempi:"
    echo "  ./run.sh build"
    echo "  ./run.sh run"
    echo "  ./run.sh search \"Avatar\""
    echo "  ./run.sh shell"
}

check_env() {
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}File .env non trovato. Creazione da .env.example...${NC}"
        if [ -f ".env.example" ]; then
            cp .env.example .env
            echo -e "${RED}ATTENZIONE: Configura le credenziali in .env prima di procedere!${NC}"
            exit 1
        else
            echo -e "${RED}Errore: .env.example non trovato${NC}"
            exit 1
        fi
    fi
}

build() {
    echo -e "${GREEN}Building Docker image...${NC}"
    docker-compose build
}

run_test() {
    check_env
    local cmd="${1:-}"

    if [ -z "$cmd" ]; then
        docker-compose run --rm mircrew-debug
    else
        docker-compose run --rm mircrew-debug python src/debug_indexer.py "$@"
    fi
}

shell() {
    check_env
    docker-compose run --rm mircrew-debug /bin/bash
}

proxy() {
    echo -e "${GREEN}Avvio mitmproxy...${NC}"
    echo "Web interface: http://localhost:8081"
    echo "Proxy: localhost:8080"
    docker-compose --profile proxy up mitmproxy
}

logs() {
    docker-compose logs -f
}

clean() {
    echo -e "${YELLOW}Rimozione container e immagini...${NC}"
    docker-compose down --rmi local -v
    echo -e "${GREEN}Pulizia completata${NC}"
}

case "${1:-help}" in
    build)
        build
        ;;
    run)
        run_test
        ;;
    basic)
        run_test basic
        ;;
    cloudscraper)
        run_test cloudscraper
        ;;
    login)
        run_test login
        ;;
    search)
        shift
        run_test search "$@"
        ;;
    selenium)
        run_test selenium
        ;;
    shell)
        shell
        ;;
    proxy)
        proxy
        ;;
    logs)
        logs
        ;;
    clean)
        clean
        ;;
    help|--help|-h)
        print_help
        ;;
    *)
        echo -e "${RED}Comando sconosciuto: $1${NC}"
        print_help
        exit 1
        ;;
esac
