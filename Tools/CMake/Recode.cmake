if(WIN64)
	option(OPTION_RECODE "Enable support for Recode" OFF)

	if(OPTION_RECODE)
		if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/recode.lic")
			file(COPY "${CMAKE_CURRENT_SOURCE_DIR}/recode.lic" DESTINATION "${CMAKE_BINARY_DIR}" NO_SOURCE_PERMISSIONS)
		endif()

		get_filename_component(RECODE_INSTALL_PATH "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Wow6432Node\\Indefiant\\Recode;InstallDir]" ABSOLUTE)
		file(TO_NATIVE_PATH "${RECODE_INSTALL_PATH}" RECODE_INSTALL_PATH)
		if(WIN64)
			set(RECODE_INSTALL_PATH "${RECODE_INSTALL_PATH}\\x64" CACHE INTERNAL "Recode path" FORCE)
		elseif(WIN32 AND EXISTS "${RECODE_INSTALL_PATH}\\Win32") # Recode 3 does not support 32-bit
			set(RECODE_INSTALL_PATH "${RECODE_INSTALL_PATH}\\Win32" CACHE INTERNAL "Recode path" FORCE)
		else()
			set(RECODE_INSTALL_PATH)
		endif()

		foreach(t IN ITEMS STATIC SHARED EXE MODULE)
			foreach(c IN ITEMS ${CMAKE_CONFIGURATION_TYPES})
				string(TOUPPER "${c}" c_upper)
				patch_recode_linker_flags(CMAKE_${t}_LINKER_FLAGS_${c_upper})
				set(CMAKE_${t}_LINKER_FLAGS_${c_upper} ${CMAKE_${t}_LINKER_FLAGS_${c_upper}} CACHE STRING "${c} link flags" FORCE)
			endforeach()
		endforeach()
		
	endif()
endif()
