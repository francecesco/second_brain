cmake_minimum_required(VERSION 3.16)
# Limita il grafo dei componenti al minimo indispensabile: sul target linux,
# senza questa restrizione ESP-IDF prova a compilare l'intero albero dei
# componenti (incluso esp-tls), che su macOS non builda per un problema noto
# con la macro __containerof. Il test host non ha bisogno di rete/TLS.
set(COMPONENTS main)
if(APPLE)
    # Su macOS getentropy() e' dichiarata in <sys/random.h> (non in <unistd.h>
    # come su Linux): components/esp_hw_support/port/linux/esp_random.c di
    # ESP-IDF 5.3.1 include solo <unistd.h> e non compila su questo host senza
    # questo include forzato.
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -include sys/random.h" CACHE STRING "" FORCE)
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(host_test)
